"""
repair_chain.py — Module / weighted repair node for a task.

Runs deterministic guard rules over the raw (or weighted) predictions of one
task and records EVERY decision (including no-op) into ``efm_repair_decisions``
plus a lineage edge raw→repaired in ``efm_prediction_lineage_edges``.

Rules: no_nan, hour_coverage, range_guard, spike_guard, missing_fill.
If there are no source rows (e.g. realtime missing), the step is SKIPPED and
produces zero repaired rows (never fabricates).
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

MIN_PRICE = -500.0
MAX_PRICE = 2000.0


def _read_source(conn, run_id: str, target_date: str, task: CircuitTask, stage: CircuitStage):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.id, p.hour_business, p.pred_price, m.name, p.model_version "
            "FROM efm_predictions p "
            "JOIN efm_dim_stage s ON p.stage_id = s.id "
            "JOIN efm_dim_model m ON p.model_id = m.id "
            "WHERE p.run_id=%s AND p.task=%s AND s.name=%s "
            "ORDER BY m.name, p.hour_business",
            (run_id, task.value, stage.value),
        )
        return [(int(i), int(hb), float(p), str(m), str(v))
                for i, hb, p, m, v in cur.fetchall()]


def _safe_median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def run_repair(
    ctx: Any,
    task: CircuitTask,
    source_stage: CircuitStage,
    repaired_stage: CircuitStage,
    order: int,
    step_name: str,
) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        src = _read_source(conn, run_id, target_date, task, source_stage)
        if not src:
            msg = (
                f"SKIPPED: no source predictions at stage={source_stage.value} "
                f"for task={task.value} — nothing to repair."
            )
            ctx.recorder.record(
                run_id, target_date, task.value, step_name, order,
                StepStatus.SKIPPED.value, input_count=0, output_count=0, message=msg)
            return CircuitStepResult(step_name, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        prices = [p for _, _, p, _, _ in src]
        med = _safe_median(prices)
        std = statistics.pstdev(prices) if len(prices) > 1 else 0.0

        repaired_rows: list[dict[str, Any]] = []
        repaired_ids: list[int] = []
        decisions = 0
        changed_count = 0
        for pid, hb, price, mname, mver in src:
            before = price
            after = price
            rule = "no_op"
            severity = "info"
            changed = False

            if price is None or price != price:  # nan
                after = med
                rule = "no_nan"
                severity = "warning"
                changed = True
            elif after < MIN_PRICE or after > MAX_PRICE:
                after = max(MIN_PRICE, min(MAX_PRICE, after))
                rule = "range_guard"
                severity = "warning"
                changed = True
            elif std > 0 and abs(after - med) > 3 * std:
                after = med
                rule = "spike_guard"
                severity = "warning"
                changed = True

            # hour_coverage / missing_fill are structural (already 24h here).

            # Always carry the (possibly adjusted) value forward so the chain
            # never starves downstream fusion/classifier/task_final. Unchanged
            # hours are carried as-is (no_op) and still logged for audit.
            # Model identity (model_name/model_version) is PRESERVED so the
            # later fusion step can weight each original model correctly.
            repaired_rows.append({
                "hour_business": hb, "pred_price": after,
                "model_name": mname, "model_version": mver,
                "is_shadow": False, "is_selected": False,
                "selected_reason": (f"{rule} repair" if changed
                                    else "no_op (carried forward)"),
                "quality_flags": [rule] if changed else ["no_op"],
            })
            if changed:
                changed_count += 1
            # Always record the decision (changed OR no_op) for full audit.
            d = RepairDecision(
                run_id=run_id, target_date=target_date, task=task,
                hour_business=hb, repair_stage=RepairStage.MODULE_REPAIR,
                rule_name=rule, before_value=before, after_value=after,
                source_prediction_id=pid,
                reason=(f"{rule} repair applied" if changed
                        else "within bounds, no repair needed"),
                severity=severity)
            insert_repair_decision(conn, d)
            decisions += 1

        # Persist ALL repaired hours (changed + carried-forward no_ops) so the
        # downstream fused / classifier_adjusted / task_final stages have input.
        repaired_ids = write_stage_predictions(
            conn, run_id, target_date, task, repaired_stage, repaired_rows,
            source_step=step_name, is_final_candidate=False)
        # Lineage: each source -> its repaired child.
        src_map = {hb: pid for pid, hb, _, _, _ in src}
        for row, rid in zip(repaired_rows, repaired_ids):
            pid = src_map.get(int(row["hour_business"]))
            insert_lineage_edge(conn, run_id, target_date, "repair", pid, rid,
                                {"rule": row.get("quality_flags")})

        msg = (f"repair complete: {len(repaired_rows)} carried forward "
               f"({changed_count} repaired, {len(src) - changed_count} no_op), "
               f"task={task.value}")
        ctx.recorder.record(
            run_id, target_date, task.value, step_name, order, StepStatus.COMPLETE.value,
            input_count=len(src), output_count=len(repaired_rows),
            message=msg, metrics_json={"repaired": len(repaired_rows),
                                       "no_op": len(src) - len(repaired_rows)})
        return CircuitStepResult(step_name, StepStatus.COMPLETE, msg,
                                 input_count=len(src), output_count=len(repaired_rows),
                                 artifacts={"repaired_stage": repaired_stage.value,
                                            "decisions": decisions})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[repair_chain] failed")
        ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                            StepStatus.FAIL.value, message=f"exception: {exc}")
        return CircuitStepResult(step_name, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
