"""
delivery_chain.py — Cross-task fusion (step 13) and Delivery final (step 15).

Step 13 (cross_task_fusion): combine the day-ahead final and real-time final.
  * When BOTH are present: the combined value follows the 2.5 delivery policy
    (real-time price uses the UNCORRECTED fusion; see reverse-engineering doc).
    For this skeleton the real-time final is absent, so the combined value is
    the day-ahead final with an explicit ``dayahead_only_fallback`` policy.
  * The combined price is written to ``efm_predictions`` stage=cross_task_fusion.

Step 15 (delivery_final): read the separator-repaired price and write the
authoritative ``efm_delivery_finals`` rows with explicit provenance
(dayahead_final_id / realtime_final_id / separator_rule / fallback_reason).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pipelines.production_circuit.contracts import (
    CircuitStage,
    CircuitStepResult,
    CircuitTask,
    DeliveryFinal,
    StepStatus,
)
from pipelines.production_circuit.step_recorder import (
    insert_delivery_final,
    insert_lineage_edge,
    write_stage_predictions,
)

logger = logging.getLogger(__name__)

CROSS_FUSION_ORDER = 15
CROSS_FUSION_NAME = "cross_task_fusion"
DELIVERY_ORDER = 17
DELIVERY_NAME = "delivery_final"


def _read_task_finals(conn, run_id: str, target_date: str, task: str) -> dict[int, tuple[int, float]]:
    """Return {hour: (final_id, price)} from efm_task_finals."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hour_business, final_price FROM efm_task_finals "
            "WHERE run_id=%s AND target_date=%s AND task=%s ORDER BY hour_business",
            (run_id, target_date, task),
        )
        return {int(hb): (int(i), float(p)) for i, hb, p in cur.fetchall()}


def run_cross_task_fusion(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        da = _read_task_finals(conn, run_id, target_date, "dayahead")
        rt = _read_task_finals(conn, run_id, target_date, "realtime")

        if not da:
            msg = "SKIPPED: no day-ahead final available for cross-task fusion."
            ctx.recorder.record(run_id, target_date, "fusion", CROSS_FUSION_NAME,
                                CROSS_FUSION_ORDER, StepStatus.SKIPPED.value,
                                input_count=0, output_count=0, message=msg)
            return CircuitStepResult(CROSS_FUSION_NAME, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        rows: list[dict[str, Any]] = []
        rt_present = bool(rt)
        for hb in sorted(da.keys()):
            da_id, da_price = da[hb]
            if rt_present and hb in rt:
                rt_id, rt_price = rt[hb]
                # 2.5 policy: real-time uses UNCORRECTED fusion; here we take
                # realtime final when available. (Skeleton: both present path.)
                combined = rt_price
                policy = "realtime_final"
            else:
                combined = da_price
                policy = "dayahead_only_fallback"
            rows.append({
                "hour_business": hb, "pred_price": combined,
                "model_name": "cross_task_fused", "model_version": "xf_v1",
                "is_shadow": False, "is_selected": False,
                "selected_reason": f"cross_task_fusion policy={policy}",
                "quality_flags": ["cross_task_fusion"],
            })

        ids = write_stage_predictions(conn, run_id, target_date, CircuitTask.FUSION,
                                      CircuitStage.CROSS_TASK_FUSION, rows,
                                      source_step=CROSS_FUSION_NAME,
                                      is_final_candidate=True)

        # Lineage: each day-ahead final -> combined child.
        da_map = da
        for row, xid in zip(rows, ids):
            src_id = da_map.get(int(row["hour_business"]), (None,))[0]
            insert_lineage_edge(conn, run_id, target_date, "fuse", src_id, xid,
                                {"policy": policy if not rt_present else "realtime_final"})

        status = StepStatus.COMPLETE if rt_present else StepStatus.PARTIAL
        msg = (f"cross-task fusion complete: {len(ids)} hours, "
               f"realtime_present={rt_present} → "
               f"{'full fusion' if rt_present else 'dayahead_only_fallback (NEEDS_MODEL_OUTPUT)'}")
        ctx.recorder.record(run_id, target_date, "fusion", CROSS_FUSION_NAME,
                            CROSS_FUSION_ORDER, status.value, input_count=len(da),
                            output_count=len(ids), message=msg,
                            metrics_json={"realtime_present": rt_present,
                                          "n_hours": len(ids)})
        return CircuitStepResult(CROSS_FUSION_NAME, status, msg, input_count=len(da),
                                 output_count=len(ids),
                                 artifacts={"realtime_present": rt_present})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[cross_task_fusion] failed")
        ctx.recorder.record(run_id, target_date, "fusion", CROSS_FUSION_NAME,
                            CROSS_FUSION_ORDER, StepStatus.FAIL.value,
                            message=f"exception: {exc}")
        return CircuitStepResult(CROSS_FUSION_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()


def _read_separator_repaired(conn, run_id: str, target_date: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hour_business, pred_price FROM efm_predictions "
            "WHERE run_id=%s AND target_date=%s AND task='delivery' "
            "AND stage='separator_repaired' ORDER BY hour_business",
            (run_id, target_date),
        )
        return [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]


def run_delivery_final(ctx: Any) -> CircuitStepResult:
    run_id = ctx.run_id
    target_date = ctx.target_date
    conn = ctx.db_mgr.new_connection()
    try:
        sep = _read_separator_repaired(conn, run_id, target_date)
        if not sep:
            # Fallback: read cross_task_fusion directly.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, hour_business, pred_price FROM efm_predictions "
                    "WHERE run_id=%s AND target_date=%s AND task='fusion' "
                    "AND stage='cross_task_fusion' ORDER BY hour_business",
                    (run_id, target_date),
                )
                sep = [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]

        if not sep:
            msg = "SKIPPED: no cross-task/separator price for delivery final."
            ctx.recorder.record(run_id, target_date, "delivery", DELIVERY_NAME,
                                DELIVERY_ORDER, StepStatus.SKIPPED.value,
                                input_count=0, output_count=0, message=msg)
            return CircuitStepResult(DELIVERY_NAME, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        da = _read_task_finals(conn, run_id, target_date, "dayahead")
        rt = _read_task_finals(conn, run_id, target_date, "realtime")
        rt_present = bool(rt)

        rows: list[dict[str, Any]] = []
        delivery_ids: list[int] = []
        sep_map = {hb: pid for pid, hb, _ in sep}
        for pid, hb, price in sep:
            da_id = da.get(hb, (None,))[0] if da else None
            rt_id = rt.get(hb, (None,))[0] if rt else None
            policy = "dayahead_only_fallback" if not rt_present else "full_delivery"
            df = DeliveryFinal(
                run_id=run_id, target_date=target_date, hour_business=hb,
                delivery_price=price, delivery_policy=policy,
                dayahead_final_id=da_id, realtime_final_id=rt_id,
                delivery_prediction_id=pid,
                separator_rule="separator_repaired" if any(
                    s[1] == hb for s in sep) else None,
                fallback_reason=("realtime_final_missing" if not rt_present else None))
            did = insert_delivery_final(conn, df)
            delivery_ids.append(did)
            rows.append({
                "hour_business": hb, "pred_price": price,
                "model_name": "delivery_final", "model_version": "delivery_v1",
                "is_shadow": False, "is_selected": True,
                "selected_reason": f"delivery policy={policy}",
                "quality_flags": ["delivery_final"],
            })

        # The delivery final IS the selected deliverable, so it is marked
        # is_selected=TRUE. efm_predictions.task is a legacy enum
        # (dayahead/realtime/fusion/final/shadow) and efm_prediction_batches
        # is (dayahead/realtime/fusion/delivery); 'fusion' is the common value
        # that fits this stage (distinguished by stage='delivery_final').
        write_stage_predictions(conn, run_id, target_date, CircuitTask.DELIVERY,
                                CircuitStage.DELIVERY_FINAL, rows,
                                source_step=DELIVERY_NAME, is_final_candidate=True)

        status = StepStatus.COMPLETE if rt_present else StepStatus.PARTIAL
        policy_label = "full_delivery" if rt_present else "dayahead_only_fallback (realtime_final_missing)"
        msg = (f"delivery final written: {len(delivery_ids)} hours, "
               f"policy={policy_label}")
        ctx.recorder.record(run_id, target_date, "delivery", DELIVERY_NAME,
                            DELIVERY_ORDER, status.value, input_count=len(sep),
                            output_count=len(delivery_ids), message=msg,
                            metrics_json={"realtime_present": rt_present,
                                          "policy": policy_label,
                                          "n_hours": len(delivery_ids)})
        return CircuitStepResult(DELIVERY_NAME, status, msg, input_count=len(sep),
                                 output_count=len(delivery_ids),
                                 artifacts={"realtime_present": rt_present,
                                            "delivery_ids": delivery_ids})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[delivery_final] failed")
        ctx.recorder.record(run_id, target_date, "delivery", DELIVERY_NAME,
                            DELIVERY_ORDER, StepStatus.FAIL.value,
                            message=f"exception: {exc}")
        return CircuitStepResult(DELIVERY_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()
