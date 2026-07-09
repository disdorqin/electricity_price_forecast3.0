"""
dayahead_chain.py — Day-ahead sub-chain node (Circuit step 3).

Minimal-but-correct implementation:
  * If real 2.5 day-ahead MODEL outputs exist in the ledger, load them as
    ``dayahead_raw_model`` (a genuine model candidate).
  * Otherwise we MUST NOT invent a model. We record the ``da_anchor`` (the
    day-ahead clearing price) strictly as a **benchmark candidate**
    (``benchmark_da_anchor`` stage) and flag ``MISSING_MODEL_OUTPUT`` so it
    can never be mistaken for a model prediction.

No ML training happens here — this node only wires existing artifacts into the
circuit ledger so the full DAG is observable end to end.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitStepResult,
    CircuitTask,
    StepStatus,
    TaskFinal,
)
from pipelines.production_circuit.step_recorder import write_stage_predictions

logger = logging.getLogger(__name__)

STEP_ORDER = 3
STEP_NAME = "dayahead_chain"


def _load_2_5_dayahead_model_outputs(conn, target_date: str) -> list[dict[str, Any]]:
    """Attempt to load genuine 2.5 day-ahead model predictions.

    Returns [] when none exist (the expected state for this skeleton — the
    2.5 model outputs have not been migrated yet). This function is the
    integration point for the future migration.
    """
    # Intentionally returns [] until 2.5 day-ahead outputs are migrated.
    # When available, they would be read from a model-outputs ledger and
    # mapped to rows with stage=dayahead_raw_model.
    return []


def _load_da_anchor(conn, target_date: str) -> list[dict[str, Any]]:
    """Load the day-ahead clearing price as a BENCHMARK candidate only."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hour_business, da_anchor FROM efm_actual_prices "
            "WHERE target_date=%s AND da_anchor IS NOT NULL ORDER BY hour_business",
            (target_date,),
        )
        rows = [
            {
                "hour_business": int(hb),
                "pred_price": float(v),
                "model_name": "da_anchor_benchmark",
                "model_version": "shandong_pmos",
                "is_shadow": False,
                "is_selected": False,
                "selected_reason": "benchmark_da_anchor (NOT a model)",
                "quality_flags": ["benchmark"],
            }
            for hb, v in cur.fetchall()
        ]
    return rows


def run_day_ahead_chain(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        model_rows = _load_2_5_dayahead_model_outputs(conn, target_date)
        if model_rows:
            stage = CircuitStage.DAYAHEAD_RAW_MODEL
            status = StepStatus.COMPLETE
            msg = f"day-ahead raw model outputs loaded ({len(model_rows)} rows)"
            model_available = True
        else:
            stage = CircuitStage.BENCHMARK_DA_ANCHOR
            model_rows = _load_da_anchor(conn, target_date)
            status = StepStatus.COMPLETE
            msg = (
                "MISSING_MODEL_OUTPUT: no 2.5 day-ahead model outputs available; "
                "wrote benchmark_da_anchor as a BENCHMARK candidate only "
                "(explicitly NOT a model prediction)."
            )
            model_available = False

        ids = write_stage_predictions(
            conn, run_id, target_date, CircuitTask.DAYAHEAD, stage, model_rows,
            source_step=STEP_NAME, is_final_candidate=False,
        )
        ctx.recorder.record(
            run_id, target_date, "dayahead", STEP_NAME, STEP_ORDER, status.value,
            input_count=24, output_count=len(ids), message=msg,
            metrics_json={"stage": stage.value, "model_available": model_available},
        )
        return CircuitStepResult(
            STEP_NAME, status, msg, input_count=24, output_count=len(ids),
            artifacts={"stage": stage.value, "model_available": model_available,
                       "prediction_ids": ids},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[dayahead_chain] failed")
        ctx.recorder.record(
            run_id, target_date, "dayahead", STEP_NAME, STEP_ORDER,
            StepStatus.FAIL.value, message=f"exception: {exc}")
        return CircuitStepResult(STEP_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()


# ── Day-ahead TASK FINAL (Circuit step 7) ──────────────────────────────

TASK_FINAL_ORDER = 7
TASK_FINAL_NAME = "dayahead_task_final"


def _read_stage(conn, run_id: str, target_date: str, task: CircuitTask, stage: CircuitStage):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hour_business, pred_price FROM efm_predictions "
            "WHERE run_id=%s AND target_date=%s AND task=%s AND stage=%s "
            "ORDER BY hour_business",
            (run_id, target_date, task.value, stage.value),
        )
        return [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]


def run_day_ahead_task_final(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        # Prefer classifier_adjusted; fall back to fused if classifier absent.
        rows = _read_stage(conn, run_id, target_date, CircuitTask.DAYAHEAD,
                           CircuitStage.DAYAHEAD_CLASSIFIER_ADJUSTED)
        src_stage = CircuitStage.DAYAHEAD_CLASSIFIER_ADJUSTED
        if not rows:
            rows = _read_stage(conn, run_id, target_date, CircuitTask.DAYAHEAD,
                               CircuitStage.DAYAHEAD_FUSED)
            src_stage = CircuitStage.DAYAHEAD_FUSED
        if not rows:
            msg = "SKIPPED: no day-ahead fused/classifier_adjusted predictions to finalize."
            ctx.recorder.record(run_id, target_date, "dayahead", TASK_FINAL_NAME,
                                TASK_FINAL_ORDER, StepStatus.SKIPPED.value,
                                input_count=0, output_count=0, message=msg)
            return CircuitStepResult(TASK_FINAL_NAME, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        from pipelines.production_circuit.step_recorder import (
            insert_lineage_edge, insert_task_final, write_stage_predictions,
        )
        final_rows = [{
            "hour_business": hb, "pred_price": price,
            "model_name": "dayahead_final", "model_version": "final_v1",
            "is_shadow": False, "is_selected": False,
            "selected_reason": "dayahead task final",
            "quality_flags": ["task_final"],
        } for _, hb, price in rows]
        ids = write_stage_predictions(conn, run_id, target_date, CircuitTask.DAYAHEAD,
                                      CircuitStage.DAYAHEAD_TASK_FINAL, final_rows,
                                      source_step=TASK_FINAL_NAME, is_final_candidate=True)

        src_map = {hb: pid for pid, hb, _ in rows}
        final_ids: list[int] = []
        for row, fid in zip(final_rows, ids):
            tf = TaskFinal(run_id=run_id, target_date=target_date,
                           task=CircuitTask.DAYAHEAD,
                           hour_business=int(row["hour_business"]),
                           final_price=float(row["pred_price"]),
                           final_stage=CircuitStage.DAYAHEAD_TASK_FINAL,
                           final_prediction_id=fid, source_policy="dayahead_final",
                           confidence_score=None)
            final_ids.append(insert_task_final(conn, tf))
            insert_lineage_edge(conn, run_id, target_date, "select",
                                src_map.get(int(row["hour_business"])), fid,
                                {"from_stage": src_stage.value})

        msg = f"day-ahead task final written: {len(final_ids)} hours (task_finals separated)."
        ctx.recorder.record(run_id, target_date, "dayahead", TASK_FINAL_NAME,
                            TASK_FINAL_ORDER, StepStatus.COMPLETE.value,
                            input_count=len(rows), output_count=len(final_ids),
                            message=msg, metrics_json={"n_final": len(final_ids)})
        return CircuitStepResult(TASK_FINAL_NAME, StepStatus.COMPLETE, msg,
                                 input_count=len(rows), output_count=len(final_ids),
                                 artifacts={"final_ids": final_ids})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[dayahead_task_final] failed")
        ctx.recorder.record(run_id, target_date, "dayahead", TASK_FINAL_NAME,
                            TASK_FINAL_ORDER, StepStatus.FAIL.value,
                            message=f"exception: {exc}")
        return CircuitStepResult(TASK_FINAL_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
