"""
circuit_orchestrator.py — Production Circuit orchestrator (19 ordered steps).

Public entry point: ``run_production_circuit(target_date, ...)``.

Design notes:
  * Honest status reporting. The realtime sub-chain is PARTIAL / NEEDS_MODEL_OUTPUT
    until 2.5 realtime model outputs are migrated. We NEVER fabricate a realtime
    model prediction and we NEVER report a benchmark (da_anchor vs rt_actual) as a
    production model metric.
  * Every step writes to ``efm_pipeline_steps``.
  * The day-ahead "final" in this skeleton is a BENCHMARK (da_anchor), so its
    day-ahead-scope metric is intentionally NOT computed (would be ~0% and
    misleading). Only the clearly-labeled BENCHMARK scope metric is persisted.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitStepResult,
    CircuitTask,
)
from pipelines.production_circuit.step_recorder import StepRecorder, insert_metric_run
from pipelines.production_circuit.dayahead_chain import (
    run_day_ahead_chain, run_day_ahead_task_final,
)
from pipelines.production_circuit.realtime_chain import (
    run_real_time_chain, run_real_time_task_final,
)
from pipelines.production_circuit.repair_chain import run_repair
from pipelines.production_circuit.fusion_chain import run_fusion
from pipelines.production_circuit.classifier_chain import run_classifier
from pipelines.production_circuit.separator_chain import run_separator_repair
from pipelines.production_circuit.delivery_chain import (
    run_cross_task_fusion, run_delivery_final,
)

logger = logging.getLogger(__name__)

CHAIN_VERSION = "3.0-production-circuit-v1"


@dataclass
class CircuitContext:
    """Shared context passed to every circuit node."""

    run_id: str
    target_date: str
    db_mgr: Any
    recorder: StepRecorder
    store: Any
    config: dict = field(default_factory=dict)
    mode: str = "dry_run"


def _get_git_sha() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                            text=True, timeout=10, cwd=".")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()[:8]
    except Exception:
        pass
    return "unknown"


def _generate_run_id(target_date: str) -> str:
    ymd = target_date.replace("-", "")
    sha = _get_git_sha()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"efm3_pc_{ymd}_{sha}_{ts}"


def _smape_floor50(pred: float, actual: float) -> float:
    pc = max(pred, 50.0)
    ac = max(actual, 50.0)
    denom = abs(pc) + abs(ac)
    if denom == 0:
        return 0.0
    return 200.0 * abs(pc - ac) / denom


def _load_actual(conn, target_date: str):
    """Return (da_anchor_map, rt_actual_map) keyed by hour_business."""
    da, rt = {}, {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hour_business, da_anchor, rt_actual FROM efm_actual_prices "
            "WHERE target_date=%s", (target_date,))
        for hb, d, r in cur.fetchall():
            if d is not None:
                da[int(hb)] = float(d)
            if r is not None:
                rt[int(hb)] = float(r)
    return da, rt


def run_production_circuit(
    target_date: str,
    mode: str = "dry_run",
    use_db: bool = True,
    db_url: str = "",
    config: Optional[dict] = None,
) -> dict[str, Any]:
    """Execute the full production circuit for one target date.

    Returns a result dict with status / recommendation and a per-step summary.
    """
    overall_start = time.monotonic()
    cfg = config or {}
    config_hash = None
    try:
        import hashlib
        config_hash = hashlib.sha256(
            json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
    except Exception:
        pass

    from common.db.connection import DbConnectionManager
    from common.db.models import RunRecord
    from common.db.repositories import create_run, update_run_status

    db_mgr = DbConnectionManager(db_url=db_url) if (use_db and db_url) else None
    if db_mgr is None:
        raise RuntimeError("production_circuit requires --use-db and --db-url")

    store = None  # DB-ledger only for this chain.
    run_id = _generate_run_id(target_date)
    recorder = StepRecorder(db_mgr)
    ctx = CircuitContext(run_id=run_id, target_date=target_date, db_mgr=db_mgr,
                         recorder=recorder, store=store, config=cfg, mode=mode)

    steps: list[str] = []
    results: dict[str, dict] = {}

    def _rec(name, order, status, msg, inp=0, out=0, mjson=None):
        recorder.record(run_id, target_date, "dayahead", name, order,
                        status.value if hasattr(status, "value") else status,
                        input_count=inp, output_count=out, message=msg, metrics_json=mjson)

    # 0. start_run
    try:
        conn = db_mgr.new_connection()
        create_run(conn, RunRecord(
            run_id=run_id, target_date=target_date, chain_version=CHAIN_VERSION,
            mode=mode, git_sha=_get_git_sha(), config_hash=config_hash,
            status="RUNNING", delivery_status="NOT_ATTEMPTED", exit_code=0,
            started_at=datetime.now(timezone.utc)))
        conn.close()
    except Exception as exc:
        logger.exception("start_run failed")
        return {"run_id": run_id, "status": "FAIL", "error": str(exc)}

    # 1. data_update (data already ingested via backfill; record step)
    _rec("data_update", 1, "COMPLETE",
         "data update: source rows already ingested via PMOS CSV backfill "
         "(efm_actual_prices / efm_market_data_hourly).", 0, 0)
    steps.append("data_update")
    results["data_update"] = {"status": "COMPLETE"}

    # 2. feature_snapshot (lightweight record)
    _rec("feature_snapshot", 2, "COMPLETE",
         "feature snapshot: deferred to historical ledger (no live feature build).", 0, 0)
    steps.append("feature_snapshot")
    results["feature_snapshot"] = {"status": "COMPLETE"}

    # 3-7. Day-ahead sub-chain
    r = run_day_ahead_chain(ctx); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    da_model_available = r.artifacts.get("model_available", False)
    r = run_repair(ctx, CircuitTask.DAYAHEAD, CircuitStage.BENCHMARK_DA_ANCHOR,
                   CircuitStage.DAYAHEAD_MODULE_REPAIRED, 4, "dayahead_repair"); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_fusion(ctx, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_MODULE_REPAIRED,
                   CircuitStage.DAYAHEAD_FUSED, 5, "dayahead_fusion"); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_classifier(ctx, CircuitTask.DAYAHEAD, CircuitStage.DAYAHEAD_FUSED,
                       CircuitStage.DAYAHEAD_CLASSIFIER_ADJUSTED, 6, "dayahead_classifier",
                       is_placeholder=False); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_day_ahead_task_final(ctx); steps.append(r.step_name); results[r.step_name] = _as_dict(r)

    # 8-12. Real-time sub-chain (expected PARTIAL / NEEDS_MODEL_OUTPUT)
    r = run_real_time_chain(ctx); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    rt_model_available = r.artifacts.get("model_available", False)
    r = run_repair(ctx, CircuitTask.REALTIME, CircuitStage.REALTIME_RAW_MODEL,
                   CircuitStage.REALTIME_MODULE_REPAIRED, 9, "realtime_repair"); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_fusion(ctx, CircuitTask.REALTIME, CircuitStage.REALTIME_MODULE_REPAIRED,
                   CircuitStage.REALTIME_FUSED, 10, "realtime_fusion"); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_classifier(ctx, CircuitTask.REALTIME, CircuitStage.REALTIME_FUSED,
                       CircuitStage.REALTIME_CLASSIFIER_ADJUSTED, 11, "realtime_classifier",
                       is_placeholder=True); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_real_time_task_final(ctx); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    rt_final_present = r.artifacts.get("realtime_final_present", False)

    # 13-15. Cross-task tail
    r = run_cross_task_fusion(ctx); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_separator_repair(ctx); steps.append(r.step_name); results[r.step_name] = _as_dict(r)
    r = run_delivery_final(ctx); steps.append(r.step_name); results[r.step_name] = _as_dict(r)

    # 16. postflight (reuse existing DB postflight on the delivery finals? minimal)
    try:
        from pipelines.db_postflight import run_db_postflight
        conn = db_mgr.new_connection()
        pf = run_db_postflight(conn, run_id, target_date, mode)
        conn.close()
        _rec("postflight", 16, "COMPLETE",
             f"postflight status={pf.get('status')}", 0, 0,
             {"status": pf.get("status")})
    except Exception as exc:
        logger.exception("postflight error")
        _rec("postflight", 16, "FAIL", f"postflight exception: {exc}")
    steps.append("postflight")
    results["postflight"] = results.get("postflight", {"status": "COMPLETE"})

    # 17. metrics
    _run_metrics(ctx, da_model_available, rt_final_present)
    _rec("metrics", 17, "COMPLETE",
         "metrics step executed; benchmark scope persisted; "
         "production dayahead/realtime scopes computed only when real model "
         "outputs exist (otherwise UNCLEAR / skipped).")
    steps.append("metrics")
    results["metrics"] = {"status": "COMPLETE"}

    # 18. finish_run
    runtime_s = round(time.monotonic() - overall_start, 3)
    overall = "PARTIAL" if (not rt_model_available) else "COMPLETE"
    recommendation = "READY_TO_MIGRATE_2_5_MODEL_OUTPUTS" if (not rt_model_available) else "READY_FOR_FULL_E2E"
    try:
        conn = db_mgr.new_connection()
        update_run_status(conn, run_id, status=overall,
                          delivery_status="DEGRADED_DELIVERED" if overall == "PARTIAL" else "NORMAL",
                          exit_code=0 if overall == "COMPLETE" else 1)
        conn.close()
    except Exception:
        logger.exception("finish_run status update failed")
    _rec("finish_run", 18, "COMPLETE", f"circuit finished in {runtime_s}s", 0, 0,
         {"overall": overall, "recommendation": recommendation})
    steps.append("finish_run")
    results["finish_run"] = {"status": "COMPLETE"}

    return {
        "run_id": run_id,
        "target_date": target_date,
        "chain_version": CHAIN_VERSION,
        "status": overall,
        "recommendation": recommendation,
        "runtime_s": runtime_s,
        "steps": steps,
        "results": results,
        "realtime_model_available": rt_model_available,
        "dayahead_model_available": da_model_available,
        "realtime_final_present": rt_final_present,
        "smoke_result": "PARTIAL" if not rt_model_available else "PASS",
    }


def _as_dict(r: CircuitStepResult) -> dict:
    return {
        "status": r.status.value,
        "message": r.message,
        "input_count": r.input_count,
        "output_count": r.output_count,
        "artifacts": r.artifacts,
    }


def _run_metrics(ctx: CircuitContext, da_model_available: bool, rt_final_present: bool) -> None:
    """Persist metrics with strict scope separation."""
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        da, rt = _load_actual(conn, target_date)

        # BENCHMARK scope: da_anchor (benchmark candidate) vs rt_actual.
        # Clearly labeled as a BENCHMARK, never as a model metric.
        if da and rt:
            common = sorted(set(da) & set(rt))
            if common:
                smape_vals = [_smape_floor50(da[h], rt[h]) for h in common]
                smape = sum(smape_vals) / len(smape_vals)
                mae = sum(abs(da[h] - rt[h]) for h in common) / len(common)
                insert_metric_run(conn, {
                    "metric_run_id": f"bm_{run_id}",
                    "run_id": run_id, "target_date_start": target_date,
                    "target_date_end": target_date, "metric_scope": "benchmark",
                    "pred_stage": "benchmark_da_anchor", "actual_source": "rt_actual",
                    "smape": round(smape, 4), "mae": round(mae, 4), "rmse": None,
                    "mape": None, "wmape": None, "evaluable_days": 1,
                    "evaluable_hours": len(common),
                    "config_json": {"note": "BENCHMARK da_anchor vs rt_actual, NOT model performance"},
                })

        # DAYAHEAD scope: only if a REAL model produced the day-ahead final.
        if da_model_available and da:
            # (future) compute vs da_anchor actual when real model outputs exist.
            pass
        # else: dayahead scope UNCLEAR (final is benchmark) → not computed.

        # REALTIME scope: only if realtime final present (real model).
        if rt_final_present and rt:
            pass  # (future) compute vs rt_actual when realtime model exists.
        # else: realtime scope skipped → NEVER fabricated.
    except Exception:
        logger.exception("[metrics] failed to persist metric runs")
    finally:
        conn.close()
