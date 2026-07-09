"""
fusion_chain.py — Fusion node for a task.

Fuses the repaired candidate predictions into a single fused value per hour.
When only ONE candidate is present (the expected skeleton state), this is a
``single_candidate_fusion``: the fused value equals the candidate and the
candidate is recorded as selected with weight 1.0.

EVERY candidate (including the single one) is persisted to ``efm_fusion_candidates``
so fusion is fully auditable. If there are no source rows, the step is SKIPPED
(never fabricates a fusion).
"""

from __future__ import annotations

import logging
from typing import Any

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitStepResult,
    CircuitTask,
    FusionCandidate,
    StepStatus,
)
from pipelines.production_circuit.step_recorder import (
    insert_fusion_candidate,
    insert_lineage_edge,
    write_stage_predictions,
)

logger = logging.getLogger(__name__)


def _read_source(conn, run_id: str, target_date: str, task: CircuitTask, stage: CircuitStage):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hour_business, pred_price, model_name, model_version "
            "FROM efm_predictions "
            "WHERE run_id=%s AND target_date=%s AND task=%s AND stage=%s "
            "ORDER BY hour_business",
            (run_id, target_date, task.value, stage.value),
        )
        return [(int(i), int(hb), float(p), str(m), str(v))
                for i, hb, p, m, v in cur.fetchall()]


def run_fusion(
    ctx: Any,
    task: CircuitTask,
    source_stage: CircuitStage,
    fused_stage: CircuitStage,
    order: int,
    step_name: str,
) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        src = _read_source(conn, run_id, target_date, task, source_stage)
        if not src:
            msg = (f"SKIPPED: no repaired predictions at stage={source_stage.value} "
                   f"for task={task.value} — fusion cannot run.")
            ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                                StepStatus.SKIPPED.value, input_count=0,
                                output_count=0, message=msg)
            return CircuitStepResult(step_name, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        n = len(src)
        fused_rows: list[dict[str, Any]] = []
        fused_ids: list[int] = []
        for idx, (pid, hb, price, mname, mver) in enumerate(src):
            weight = 1.0 / n if n > 0 else 1.0
            cand = FusionCandidate(
                run_id=run_id, target_date=target_date, task=task,
                hour_business=hb, candidate_model=mname,
                candidate_stage=source_stage, candidate_prediction_id=pid,
                weight_value=weight, rank_value=idx + 1,
                score_json={"mode": "single_candidate_fusion" if n == 1 else "weighted_fusion",
                            "n_candidates": n},
                selected=True, rejected_reason=None if n == 1 else None)
            insert_fusion_candidate(conn, cand)

            fused_rows.append({
                "hour_business": hb, "pred_price": price,
                "model_name": f"{task.value}_fused", "model_version": "fusion_v1",
                "is_shadow": False, "is_selected": False,
                "selected_reason": f"fused from {n} candidate(s)",
                "quality_flags": ["fused"],
            })

        fused_ids = write_stage_predictions(
            conn, run_id, target_date, task, fused_stage, fused_rows,
            source_step=step_name, is_final_candidate=True)

        # Lineage: each source -> its fused child.
        for (pid, hb, _, _, _), fid in zip(src, fused_ids):
            insert_lineage_edge(conn, run_id, target_date, "fuse", pid, fid,
                                {"n_candidates": n})

        mode = "single_candidate_fusion" if n == 1 else "weighted_fusion"
        msg = f"fusion complete ({mode}): {n} candidate(s), {len(fused_ids)} fused hours"
        ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                            StepStatus.COMPLETE.value, input_count=n,
                            output_count=len(fused_ids), message=msg,
                            metrics_json={"n_candidates": n, "mode": mode})
        return CircuitStepResult(step_name, StepStatus.COMPLETE, msg,
                                 input_count=n, output_count=len(fused_ids),
                                 artifacts={"fused_stage": fused_stage.value,
                                            "n_candidates": n, "mode": mode})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[fusion_chain] failed")
        ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                            StepStatus.FAIL.value, message=f"exception: {exc}")
        return CircuitStepResult(step_name, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
