"""
classifier_chain.py — Classifier / repair-adjust node for a task.

Production semantics (per 2.5 reverse-engineering):
  * The classifier is REAL-TIME-ONLY. The day-ahead sub-chain therefore has
    no real classifier; its ``classifier_adjusted`` stage is a legitimate
    pass-through of the fused value.
  * The real-time classifier logic has NOT been migrated yet. Until then it is
    a PLACEHOLDER: it still emits a ``classifier_adjusted`` stage for audit
    continuity, but the step is recorded as SKIPPED (never COMPLETE) so it is
    never mistaken for a working classifier.

Neither branch fabricates a model; both only copy/mirror already-fused values.
"""

from __future__ import annotations

import logging
from typing import Any

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitStepResult,
    CircuitTask,
    StepStatus,
)
from pipelines.production_circuit.step_recorder import (
    insert_lineage_edge,
    write_stage_predictions,
)

logger = logging.getLogger(__name__)


def _read_fused(conn, run_id: str, target_date: str, task: CircuitTask, stage: CircuitStage):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.id, p.hour_business, p.pred_price FROM efm_predictions p "
            "JOIN efm_dim_stage s ON p.stage_id = s.id "
            "WHERE p.run_id=%s AND p.task=%s AND s.name=%s "
            "ORDER BY p.hour_business",
            (run_id, task.value, stage.value),
        )
        return [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]


def run_classifier(
    ctx: Any,
    task: CircuitTask,
    fused_stage: CircuitStage,
    adjusted_stage: CircuitStage,
    order: int,
    step_name: str,
    is_placeholder: bool,
) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        fused = _read_fused(conn, run_id, target_date, task, fused_stage)
        if not fused:
            msg = (f"SKIPPED: no fused predictions at stage={fused_stage.value} "
                   f"for task={task.value} — classifier cannot run.")
            ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                                StepStatus.SKIPPED.value, input_count=0,
                                output_count=0, message=msg)
            return CircuitStepResult(step_name, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        rows = [{
            "hour_business": hb, "pred_price": price,
            "model_name": f"{task.value}_classifier_adj", "model_version": "classifier_v0",
            "is_shadow": False, "is_selected": False,
            "selected_reason": "classifier pass-through (placeholder)" if is_placeholder
                               else "classifier realtime-only pass-through",
            "quality_flags": ["classifier_placeholder"] if is_placeholder else ["classifier_passthrough"],
        } for _, hb, price in fused]
        ids = write_stage_predictions(
            conn, run_id, target_date, task, adjusted_stage, rows,
            source_step=step_name, is_final_candidate=True)

        # Lineage fused -> classifier_adjusted.
        fused_map = {hb: pid for pid, hb, _ in fused}
        for row, cid in zip(rows, ids):
            insert_lineage_edge(conn, run_id, target_date, "classifier_adjust",
                                fused_map.get(int(row["hour_business"])), cid, {})

        if is_placeholder:
            status = StepStatus.SKIPPED
            msg = (f"PLACEHOLDER: 2.5 real-time classifier NOT migrated. "
                   f"Wrote classifier_adjusted as pass-through for audit continuity; "
                   f"step is SKIPPED (NOT a real classifier output). {len(ids)} hours.")
        else:
            status = StepStatus.COMPLETE
            msg = (f"classifier pass-through (realtime-only; dayahead has none). "
                   f"{len(ids)} hours mirrored to classifier_adjusted.")

        ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                            status.value, input_count=len(fused),
                            output_count=len(ids), message=msg,
                            metrics_json={"placeholder": is_placeholder})
        return CircuitStepResult(step_name, status, msg, input_count=len(fused),
                                 output_count=len(ids),
                                 artifacts={"adjusted_stage": adjusted_stage.value})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[classifier_chain] failed")
        ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                            StepStatus.FAIL.value, message=f"exception: {exc}")
        return CircuitStepResult(step_name, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
