"""
realtime_chain.py — Real-time sub-chain node (Circuit step 8).

CRITICAL RULE (per task spec):
  * If genuine realtime model outputs exist, load them as ``realtime_raw_model``.
  * If they DO NOT exist, this step MUST be recorded as SKIPPED / PARTIAL with
    a ``NEEDS_MODEL_OUTPUT`` signal. It must NEVER fall back to ``da_anchor``
    and pretend it is a realtime model prediction.

This skeleton expects NO realtime model outputs to be present yet, so the
step reports PARTIAL (NEEDS_MODEL_OUTPUT) and produces zero realtime rows.
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
from pipelines.production_circuit.step_recorder import write_stage_predictions

logger = logging.getLogger(__name__)

STEP_ORDER = 8
STEP_NAME = "realtime_chain"


def _load_2_5_realtime_model_outputs(conn, target_date: str) -> list[dict[str, Any]]:
    """Attempt to load genuine 2.5 realtime candidate predictions.

    Returns [] when none exist (expected for this skeleton). This is the
    integration point for the future 2.5 realtime model-output migration.
    RT916 / TimeMixer are intentionally EXCLUDED from the online critical
    path per 3.0 design (they are long-training models; only fast candidates
    belong here).
    """
    return []


def run_real_time_chain(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        model_rows = _load_2_5_realtime_model_outputs(conn, target_date)
        if model_rows:
            ids = write_stage_predictions(
                conn, run_id, target_date, CircuitTask.REALTIME,
                CircuitStage.REALTIME_RAW_MODEL, model_rows,
                source_step=STEP_NAME, is_final_candidate=False,
            )
            status = StepStatus.COMPLETE
            msg = f"realtime raw model outputs loaded ({len(ids)} rows)"
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
            metrics_json={"stage": "realtime_raw_model", "model_available": model_available},
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

TASK_FINAL_ORDER = 12
TASK_FINAL_NAME = "realtime_task_final"


def _read_stage(conn, run_id: str, target_date: str, task: CircuitTask, stage: CircuitStage):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hour_business, pred_price FROM efm_predictions "
            "WHERE run_id=%s AND target_date=%s AND task=%s AND stage=%s "
            "ORDER BY hour_business",
            (run_id, target_date, task.value, stage.value),
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
            # Realtime model output was missing → NO realtime final.
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
