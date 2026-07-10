"""
negative_price_fixer.py — Real-time negative-price / spike residual fixer.

Runs on the fused real-time prediction (Circuit step between fusion and the
real-time classifier). It is the circuit's "负电价修整器":

  * If a P3 spike_residual correction file is available for the date
    (``config["spike_residual_dir"]/<target_date>.csv`` with columns
    ``hour_business, original_pred, corrected_pred, applied``), the corrected
    value is applied where ``applied == True`` (the P3 two-stage GBM corrector
    from docs/p3_spike_residual_final_report.md).
  * Otherwise a conservative rule-based guard runs: only implausibly low
    values (below NEG_FLOOR) are clamped; legitimate negative prices in
    山东 (Shandong) are preserved (we do NOT zero them out).
  * Every decision (correction OR no_op) is recorded in efm_repair_decisions
    with repair_stage=negative_price for full audit.

If there is no fused real-time input, the step is SKIPPED (never fabricates).
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Any

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitStepResult,
    CircuitTask,
    RepairDecision,
    RepairStage,
    StepStatus,
)
from pipelines.production_circuit.step_recorder import (
    insert_lineage_edge,
    insert_repair_decision,
    write_stage_predictions,
)

logger = logging.getLogger(__name__)

STEP_ORDER = 11
STEP_NAME = "realtime_negative_price_fixer"

NEG_FLOOR = -500.0  # only clamp absurd negatives; real 山东 negatives are kept.


def _read_fused(conn, run_id: str, target_date: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.id, p.hour_business, p.pred_price FROM efm_predictions p "
            "JOIN efm_dim_stage s ON p.stage_id = s.id "
            "WHERE p.run_id=%s AND p.task='realtime' "
            "AND s.name='realtime_fused' ORDER BY p.hour_business",
            (run_id,),
        )
        return [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]


def _load_p3_corrections(config: dict, target_date: str) -> dict[int, float]:
    """Return {hour_business: corrected_pred} for applied P3 corrections."""
    out: dict[int, float] = {}
    base = config.get("spike_residual_dir")
    if not base:
        return out
    path = os.path.join(base, f"{target_date}.csv")
    if not os.path.exists(path):
        return out
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            lower = {c.lower(): c for c in (reader.fieldnames or [])}
            hb_c = lower.get("hour_business") or lower.get("hour")
            orig_c = lower.get("original_pred")
            corr_c = lower.get("corrected_pred")
            app_c = lower.get("applied")
            if not (hb_c and corr_c):
                return out
            for row in reader:
                applied = str(row.get(app_c, "True")).strip().lower() in ("1", "true", "yes")
                if not applied:
                    continue
                try:
                    out[int(float(row[hb_c]))] = float(row[corr_c])
                except (TypeError, ValueError):
                    continue
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[negative_price_fixer] failed to read P3 corrections: %s", exc)
    return out


def run_negative_price_fixer(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        fused = _read_fused(conn, run_id, target_date)
        if not fused:
            msg = "SKIPPED: no realtime_fused input for negative-price fixer."
            ctx.recorder.record(run_id, target_date, "realtime", STEP_NAME,
                                STEP_ORDER, StepStatus.SKIPPED.value,
                                input_count=0, output_count=0, message=msg)
            return CircuitStepResult(STEP_NAME, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        corr = _load_p3_corrections(ctx.config, target_date)
        used_p3 = bool(corr)

        out_rows: list[dict[str, Any]] = []
        out_ids: list[int] = []
        decisions = 0
        corrected = 0
        for pid, hb, price in fused:
            before = price
            after = price
            rule = "no_op"
            if hb in corr:
                after = corr[hb]
                rule = "spike_residual_corrected"
            elif after < NEG_FLOOR:
                after = NEG_FLOOR
                rule = "negative_floor"
            if after != before:
                corrected += 1
            insert_repair_decision(conn, RepairDecision(
                run_id=run_id, target_date=target_date, task=CircuitTask.REALTIME,
                hour_business=hb, repair_stage=RepairStage.NEGATIVE_PRICE,
                rule_name=rule, before_value=before, after_value=after,
                source_prediction_id=pid,
                reason=(f"P3 spike_residual correction applied" if rule == "spike_residual_corrected"
                        else "implausible negative clamped" if rule == "negative_floor"
                        else "within bounds, no fix needed"),
                severity="warning" if rule != "no_op" else "info"))
            decisions += 1
            out_rows.append({
                "hour_business": hb, "pred_price": after,
                "model_name": "rt_negative_fixed", "model_version": "negfix_v1",
                "is_shadow": False, "is_selected": False,
                "selected_reason": f"negative_price fixer: {rule}",
                "quality_flags": [rule],
            })

        out_ids = write_stage_predictions(
            conn, run_id, target_date, CircuitTask.REALTIME,
            CircuitStage.REALTIME_NEGATIVE_PRICE_FIXED, out_rows,
            source_step=STEP_NAME, is_final_candidate=True)

        fused_map = {hb: pid for pid, hb, _ in fused}
        for row, oid in zip(out_rows, out_ids):
            insert_lineage_edge(conn, run_id, target_date, "negative_fix",
                                fused_map.get(int(row["hour_business"])), oid,
                                {"rule": row.get("quality_flags")})

        src = "P3 spike_residual" if used_p3 else "rule-based"
        msg = (f"negative-price fixer complete ({src}): {len(out_ids)} hours "
               f"({corrected} corrected, {len(out_ids) - corrected} no_op).")
        ctx.recorder.record(run_id, target_date, "realtime", STEP_NAME,
                            STEP_ORDER, StepStatus.COMPLETE.value,
                            input_count=len(fused), output_count=len(out_ids),
                            message=msg, metrics_json={"corrected": corrected,
                                                       "source": src})
        return CircuitStepResult(STEP_NAME, StepStatus.COMPLETE, msg,
                                 input_count=len(fused), output_count=len(out_ids),
                                 artifacts={"source": src, "corrected": corrected})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[negative_price_fixer] failed")
        ctx.recorder.record(run_id, target_date, "realtime", STEP_NAME,
                            STEP_ORDER, StepStatus.FAIL.value,
                            message=f"exception: {exc}")
        return CircuitStepResult(STEP_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
