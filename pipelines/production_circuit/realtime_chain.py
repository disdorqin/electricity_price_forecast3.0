"""
realtime_chain.py — Real-time sub-chain node (Circuit step 8).

Wired to OUR real-time fusion objects: SGDFNet + TimesFM, plus the derived
``da_aware_sgdf_selector`` candidate (defaults to DA_anchor, switches to
SGDFNet only at high-confidence non-winter windows — see
configs/candidate_registry/realtime_da_sgdf_selector.yaml). RT916 / TimeMixer
are intentionally EXCLUDED from the online critical path per 3.0 design.

Honest-status rule:
  * If any realtime model output exists -> load them as ``realtime_raw_model``
    and proceed (COMPLETE).
  * If NONE exist -> record PARTIAL / NEEDS_MODEL_OUTPUT, produce ZERO rows,
    and NEVER fall back to da_anchor as a fake realtime prediction.
"""

from __future__ import annotations

import logging
from typing import Any

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitStepResult,
    CircuitTask,
    StepStatus,
    TaskFinal,
)
from pipelines.production_circuit.model_loader import (
    DEFAULT_REALTIME_MODELS,
    derive_da_aware_selector,
    load_model_outputs,
)
from pipelines.production_circuit.step_recorder import write_stage_predictions

logger = logging.getLogger(__name__)

STEP_ORDER = 8
STEP_NAME = "realtime_chain"


def run_real_time_chain(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    rt_models: list[str] = ctx.config.get("realtime_models") or DEFAULT_REALTIME_MODELS
    # The selector is derived, not ingested; strip it from the DB load list.
    db_models = [m for m in rt_models if m != "da_aware_sgdf_selector"]
    conn = ctx.db_mgr.new_connection()
    try:
        model_rows = load_model_outputs(conn, run_id, target_date,
                                        CircuitTask.REALTIME, db_models)
        # Derive the DA-aware SGDFNet selector candidate from sgdfnet + da_anchor.
        sgdfnet_by_hour: dict[int, float] = {
            int(r["hour_business"]): float(r["pred_price"])
            for r in model_rows if r["model_name"] == "sgdfnet"
        }
        if "da_aware_sgdf_selector" in rt_models:
            selector_rows = derive_da_aware_selector(conn, target_date, sgdfnet_by_hour)
            model_rows = model_rows + selector_rows

        if model_rows:
            ids = write_stage_predictions(
                conn, run_id, target_date, CircuitTask.REALTIME,
                CircuitStage.REALTIME_RAW_MODEL, model_rows,
                source_step=STEP_NAME, is_final_candidate=False,
            )
            status = StepStatus.COMPLETE
            present = sorted(set(r["model_name"] for r in model_rows))
            msg = (f"realtime model outputs loaded ({len(ids)} rows from "
                   f"{len(present)} candidate(s): {present})")
            model_available = True
        else:
            # DO NOT fabricate. Record PARTIAL / NEEDS_MODEL_OUTPUT.
            ids = []
            status = StepStatus.PARTIAL
            msg = (
                "NEEDS_MODEL_OUTPUT: no realtime model outputs available. "
                "Step recorded as PARTIAL — realtime sub-chain cannot proceed. "
                "da_anchor was NOT used as a realtime model (per design rule)."
            )
            model_available = False

        ctx.recorder.record(
            run_id, target_date, "realtime", STEP_NAME, STEP_ORDER, status.value,
            input_count=24, output_count=len(ids), message=msg,
            metrics_json={"stage": "realtime_raw_model", "model_available": model_available,
                          "n_models": len(set(r["model_name"] for r in model_rows))},
        )
        return CircuitStepResult(
            STEP_NAME, status, msg, input_count=24, output_count=len(ids),
            artifacts={"stage": "realtime_raw_model", "model_available": model_available,
                       "prediction_ids": ids},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[realtime_chain] failed")
        ctx.recorder.record(
            run_id, target_date, "realtime", STEP_NAME, STEP_ORDER,
            StepStatus.FAIL.value, message=f"exception: {exc}")
        return CircuitStepResult(STEP_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()


# ── Real-time TASK FINAL (Circuit step 12) ─────────────────────────────

TASK_FINAL_ORDER = 13
TASK_FINAL_NAME = "realtime_task_final"


def _read_stage(conn, run_id: str, target_date: str, task: CircuitTask, stage: CircuitStage):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.id, p.hour_business, p.pred_price FROM efm_predictions p "
            "JOIN efm_dim_stage s ON p.stage_id = s.id "
            "WHERE p.run_id=%s AND p.task=%s AND s.name=%s "
            "ORDER BY p.hour_business",
            (run_id, task.value, stage.value),
        )
        return [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]


def run_real_time_task_final(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        rows = _read_stage(conn, run_id, target_date, CircuitTask.REALTIME,
                           CircuitStage.REALTIME_CLASSIFIER_ADJUSTED)
        src_stage = CircuitStage.REALTIME_CLASSIFIER_ADJUSTED
        if not rows:
            rows = _read_stage(conn, run_id, target_date, CircuitTask.REALTIME,
                               CircuitStage.REALTIME_FUSED)
            src_stage = CircuitStage.REALTIME_FUSED

        if not rows:
            msg = ("SKIPPED: no realtime fused/classifier_adjusted predictions. "
                   "Realtime task final is ABSENT (NEEDS_MODEL_OUTPUT). "
                   "Delivery will fallback to day-ahead final.")
            ctx.recorder.record(run_id, target_date, "realtime", TASK_FINAL_NAME,
                                TASK_FINAL_ORDER, StepStatus.SKIPPED.value,
                                input_count=0, output_count=0, message=msg,
                                metrics_json={"realtime_final_present": False})
            return CircuitStepResult(TASK_FINAL_NAME, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0,
                                     artifacts={"realtime_final_present": False})

        from pipelines.production_circuit.step_recorder import (
            insert_lineage_edge, insert_task_final, write_stage_predictions,
        )
        final_rows = [{
            "hour_business": hb, "pred_price": price,
            "model_name": "realtime_final", "model_version": "final_v1",
            "is_shadow": False, "is_selected": False,
            "selected_reason": "realtime task final",
            "quality_flags": ["task_final"],
        } for _, hb, price in rows]
        ids = write_stage_predictions(conn, run_id, target_date, CircuitTask.REALTIME,
                                      CircuitStage.REALTIME_TASK_FINAL, final_rows,
                                      source_step=TASK_FINAL_NAME, is_final_candidate=True)

        src_map = {hb: pid for pid, hb, _ in rows}
        final_ids: list[int] = []
        for row, fid in zip(final_rows, ids):
            tf = TaskFinal(run_id=run_id, target_date=target_date,
                           task=CircuitTask.REALTIME,
                           hour_business=int(row["hour_business"]),
                           final_price=float(row["pred_price"]),
                           final_stage=CircuitStage.REALTIME_TASK_FINAL,
                           final_prediction_id=fid, source_policy="realtime_final",
                           confidence_score=None)
            final_ids.append(insert_task_final(conn, tf))
            insert_lineage_edge(conn, run_id, target_date, "select",
                                src_map.get(int(row["hour_business"])), fid,
                                {"from_stage": src_stage.value})

        msg = f"realtime task final written: {len(final_ids)} hours (separated from day-ahead)."
        ctx.recorder.record(run_id, target_date, "realtime", TASK_FINAL_NAME,
                            TASK_FINAL_ORDER, StepStatus.COMPLETE.value,
                            input_count=len(rows), output_count=len(final_ids),
                            message=msg, metrics_json={"realtime_final_present": True,
                                                       "n_final": len(final_ids)})
        return CircuitStepResult(TASK_FINAL_NAME, StepStatus.COMPLETE, msg,
                                 input_count=len(rows), output_count=len(final_ids),
                                 artifacts={"realtime_final_present": True,
                                            "final_ids": final_ids})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[realtime_task_final] failed")
        ctx.recorder.record(run_id, target_date, "realtime", TASK_FINAL_NAME,
                            TASK_FINAL_ORDER, StepStatus.FAIL.value,
                            message=f"exception: {exc}")
        return CircuitStepResult(TASK_FINAL_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
