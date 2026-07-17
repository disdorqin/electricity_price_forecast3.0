"""
fusion_chain.py — Fusion node for a task (Circuit step 5 / 10).

Fuses the repaired candidate predictions of one task into a single fused value
per hour. Supports MULTI-MODEL weighted fusion:

  * Each hour may have several candidate models (e.g. cfg05 / xgboost_rich /
    catboost_rich for day-ahead; sgdfnet / timesfm / da_aware_sgdf_selector for
    real-time). Per-model weights come from ``config["fusion_weights"]``
    (model_name -> weight); missing models default to weight 1.0, then weights
    are normalised to sum to 1 per hour.
  * This is the integration slot for the 2.5 BGEW learner
    (DailyLedgerGEF): once historical per-model SMAPE is available, the
    ``fusion_weights`` can be replaced by learned BGEW weights. For now fixed
    (registry) weights give an honest, auditable multi-model fusion.

EVERY candidate (including each model in a multi-model hour) is persisted to
``efm_fusion_candidates`` so fusion is fully auditable. If there are no source
rows, the step is SKIPPED (never fabricates a fusion).
"""

from __future__ import annotations

import logging
from collections import defaultdict
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
            "ORDER BY model_name, hour_business",
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
    weights_cfg: dict[str, float] = (ctx.config.get("fusion_weights") or {})
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

        # Group candidates by hour.
        by_hour: dict[int, list[tuple[int, float, str, str]]] = defaultdict(list)
        for pid, hb, price, mname, mver in src:
            by_hour[hb].append((pid, price, mname, mver))

        multi_model = any(len(v) > 1 for v in by_hour.values())

        fused_rows: list[dict[str, Any]] = []
        fused_ids: list[int] = []
        candidates_written = 0

        for hb in sorted(by_hour):
            cands = by_hour[hb]

            # Guard: exclude negative-prediction candidates from fusion.
            # Models that predict negative prices (a05/timesfm in midday hours)
            # would drag the weighted average far below reality. By filtering
            # them out, only non-negative candidates participate.
            non_neg_cands = [(pid, price, mname, mver) for pid, price, mname, mver in cands if price >= 0]
            if non_neg_cands:
                fusion_cands = non_neg_cands
            else:
                # All negative — keep the least-negative one to avoid fabricating
                fusion_cands = [max(cands, key=lambda x: x[1])]

            raw_w = [float(weights_cfg.get(mname, 1.0)) for (_, _, mname, _) in fusion_cands]
            s = sum(raw_w) or 1.0
            norm = [w / s for w in raw_w]
            fused_val = sum(w * price for w, (_, price, _, _) in zip(norm, fusion_cands))

            for (pid, price, mname, mver), w in zip(cands, [float(weights_cfg.get(m, 1.0)) for (_, _, m, _) in cands]):
                # Normalise weight against ALL candidates for audit, but mark
                # excluded ones with effective_weight=0.
                eff_w = float(weights_cfg.get(mname, 1.0)) / s if (pid, price, mname, mver) in fusion_cands else 0.0
                cand = FusionCandidate(
                    run_id=run_id, target_date=target_date, task=task,
                    hour_business=hb, candidate_model=mname,
                    candidate_stage=source_stage, candidate_prediction_id=pid,
                    weight_value=round(eff_w, 6), rank_value=None,
                    score_json={"mode": "multi_model_weighted_fusion" if multi_model
                                else "single_candidate_fusion",
                                "n_candidates": len(cands),
                                "raw_weight": round(float(weights_cfg.get(mname, 1.0)), 6),
                                "excluded_negative": (pid, price, mname, mver) not in fusion_cands},
                    selected=(pid, price, mname, mver) in fusion_cands,
                    rejected_reason=("negative_prediction_excluded"
                                     if (pid, price, mname, mver) not in fusion_cands
                                     else None))
                insert_fusion_candidate(conn, cand)
                candidates_written += 1

            fused_rows.append({
                "hour_business": hb, "pred_price": fused_val,
                "model_name": f"{task.value}_fused", "model_version": "fusion_v1",
                "is_shadow": False, "is_selected": False,
                "selected_reason": f"fused from {len(cands)} candidate(s) (weighted)",
                "quality_flags": ["fused"],
            })

        fused_ids = write_stage_predictions(
            conn, run_id, target_date, task, fused_stage, fused_rows,
            source_step=step_name, is_final_candidate=True)

        # Lineage: each source -> its fused child.
        for (pid, hb, _, _, _), fid in zip(src, fused_ids):
            insert_lineage_edge(conn, run_id, target_date, "fuse", pid, fid,
                                {"n_candidates": len(by_hour.get(hb, []))})

        mode = "multi_model_weighted_fusion" if multi_model else "single_candidate_fusion"
        n_models = len({m for (_, _, m, _, _) in src})
        msg = (f"fusion complete ({mode}): {n_models} model(s), "
               f"{candidates_written} candidate rows, {len(fused_ids)} fused hours")
        ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                            StepStatus.COMPLETE.value, input_count=len(src),
                            output_count=len(fused_ids), message=msg,
                            metrics_json={"n_candidates": len(src),
                                          "n_models": n_models, "mode": mode})
        return CircuitStepResult(step_name, StepStatus.COMPLETE, msg,
                                 input_count=len(src), output_count=len(fused_ids),
                                 artifacts={"fused_stage": fused_stage.value,
                                            "n_candidates": len(src),
                                            "n_models": n_models, "mode": mode})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[fusion_chain] failed")
        ctx.recorder.record(run_id, target_date, task.value, step_name, order,
                            StepStatus.FAIL.value, message=f"exception: {exc}")
        return CircuitStepResult(step_name, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
