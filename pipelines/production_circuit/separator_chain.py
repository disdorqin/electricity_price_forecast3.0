"""
separator_chain.py — Separator / final safety repair (Circuit step 14).

Applies the user-specified "分离器修补" as a final safety pass over the
cross-task-fused price. Every action (including no-op) is recorded in
``efm_repair_decisions`` with ``repair_stage='separator_repair'``.

If there is no cross-task-fusion input (should not happen after step 13), the
step is SKIPPED.
"""

from __future__ import annotations

import logging
import statistics
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

STEP_ORDER = 14
STEP_NAME = "separator_repair"

MIN_PRICE = -500.0
MAX_PRICE = 2000.0


def _read_cross_fusion(conn, run_id: str, target_date: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hour_business, pred_price FROM efm_predictions "
            "WHERE run_id=%s AND target_date=%s AND task='fusion' "
            "AND stage='cross_task_fusion' ORDER BY hour_business",
            (run_id, target_date),
        )
        return [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]


def run_separator_repair(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        src = _read_cross_fusion(conn, run_id, target_date)
        if not src:
            msg = "SKIPPED: no cross_task_fusion input for separator repair."
            ctx.recorder.record(run_id, target_date, "delivery", STEP_NAME,
                                STEP_ORDER, StepStatus.SKIPPED.value,
                                input_count=0, output_count=0, message=msg)
            return CircuitStepResult(STEP_NAME, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        prices = [p for _, _, p in src]
        med = statistics.median(prices) if prices else 0.0
        std = statistics.pstdev(prices) if len(prices) > 1 else 0.0

        out_rows: list[dict[str, Any]] = []
        out_ids: list[int] = []
        decisions = 0
        changed_count = 0
        for pid, hb, price in src:
            before = price
            after = price
            rule = "no_op"
            severity = "info"
            changed = False
            if price is None or price != price:
                after = med
                rule = "separator_no_nan"
                severity = "warning"
                changed = True
            elif after < MIN_PRICE or after > MAX_PRICE:
                after = max(MIN_PRICE, min(MAX_PRICE, after))
                rule = "separator_range_guard"
                severity = "warning"
                changed = True
            elif std > 0 and abs(after - med) > 4 * std:
                after = med
                rule = "separator_spike_guard"
                severity = "warning"
                changed = True

            # Always carry the (possibly adjusted) value forward so the chain
            # never starves the downstream cross-task-fusion / delivery step.
            # Unchanged hours are carried as-is (no_op) and still logged.
            out_rows.append({
                "hour_business": hb, "pred_price": after,
                "model_name": "delivery_separator", "model_version": "sep_v1",
                "is_shadow": False, "is_selected": False,
                "selected_reason": (f"separator {rule}" if changed
                                    else "no_op (carried forward)"),
                "quality_flags": [rule] if changed else ["no_op"],
            })
            if changed:
                changed_count += 1
            # Always record the decision (changed OR no_op) for full audit.
            insert_repair_decision(conn, RepairDecision(
                run_id=run_id, target_date=target_date, task=CircuitTask.DELIVERY,
                hour_business=hb, repair_stage=RepairStage.SEPARATOR_REPAIR,
                rule_name=rule, before_value=before, after_value=after,
                source_prediction_id=pid,
                reason=(f"separator {rule} applied" if changed
                        else "separator within bounds, no repair needed"),
                severity=severity))
            decisions += 1

        # Persist ALL separator-repaired hours (changed + carried-forward
        # no_ops) so the downstream delivery_final stage always has an input.
        # NOTE: efm_predictions.task is a legacy enum (dayahead/realtime/
        # fusion/final/shadow) and efm_prediction_batches.task is
        # (dayahead/realtime/fusion/delivery); the only common value that
        # fits this intermediate stage is 'fusion' (distinguished by stage).
        out_ids = write_stage_predictions(
            conn, run_id, target_date, CircuitTask.DELIVERY,
            CircuitStage.SEPARATOR_REPAIRED, out_rows,
            source_step=STEP_NAME, is_final_candidate=True)
        src_map = {hb: pid for pid, hb, _ in src}
        for row, oid in zip(out_rows, out_ids):
            insert_lineage_edge(conn, run_id, target_date, "separator_adjust",
                                src_map.get(int(row["hour_business"])), oid,
                                {"rule": row.get("quality_flags")})

        msg = (f"separator repair complete: {len(out_rows)} carried forward "
               f"({changed_count} repaired, {len(src) - changed_count} no_op).")
        ctx.recorder.record(run_id, target_date, "delivery", STEP_NAME, STEP_ORDER,
                            StepStatus.COMPLETE.value, input_count=len(src),
                            output_count=len(out_ids), message=msg,
                            metrics_json={"repaired": changed_count,
                                          "no_op": len(src) - changed_count})
        return CircuitStepResult(STEP_NAME, StepStatus.COMPLETE, msg,
                                 input_count=len(src), output_count=len(out_ids),
                                 artifacts={"decisions": decisions})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[separator_chain] failed")
        ctx.recorder.record(run_id, target_date, "delivery", STEP_NAME, STEP_ORDER,
                            StepStatus.FAIL.value, message=f"exception: {exc}")
        return CircuitStepResult(STEP_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
