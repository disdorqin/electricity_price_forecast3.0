"""
negcorr_chain.py — NegCorr negative-price correction step (V3.1).

Inserted between fusion (step 10) and negative_price_fixer (step 12) in the
production circuit. Operates on the FUSED real-time output (not on individual
model candidates) and applies the NegCorrShadowModule correction.

Feature-flag tri-state (config["negcorr_mode"] or env EFM3_ENABLE_NEGCORR):
  * off (DEFAULT) → passthrough: fusion output copied to negcorr_corrected unchanged
  * shadow        → compute NegCorr, log to shadow, output unchanged
  * production    → apply NegCorr correction, write corrected values

All failure paths are fail-closed: the fusion output is returned unchanged.
"""

from __future__ import annotations

import json
import logging
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
STEP_NAME = "realtime_negcorr"


def _read_fused(conn, run_id: str, target_date: str):
    """Read the realtime_fused prediction rows."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hour_business, pred_price FROM efm_predictions "
            "WHERE run_id=%s AND target_date=%s AND task='realtime' "
            "AND stage='realtime_fused' ORDER BY hour_business",
            (run_id, target_date),
        )
        return [(int(i), int(hb), float(p)) for i, hb, p in cur.fetchall()]


def _read_da_anchor(conn, target_date: str) -> dict[int, float]:
    """Read da_anchor for da_pred input to NegCorr."""
    out: dict[int, float] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hour_business, da_anchor FROM efm_actual_prices "
            "WHERE target_date=%s AND da_anchor IS NOT NULL ORDER BY hour_business",
            (target_date,),
        )
        for hb, v in cur.fetchall():
            out[int(hb)] = float(v)
    return out


def _negcorr_mode(config: dict) -> str:
    """Resolve NegCorr mode from config or env, defaulting to 'off'."""
    # First check config (circuit config overrides env)
    mode = config.get("negcorr_mode", "").strip().lower()
    if mode in ("off", "shadow", "production"):
        return mode
    # Fall back to env
    import os
    env_mode = os.environ.get("EFM3_ENABLE_NEGCORR", "off").strip().lower()
    if env_mode in ("off", "shadow", "production"):
        return env_mode
    return "off"


def run_negcorr_correction(ctx: Any) -> CircuitStepResult:
    """Run NegCorr correction on the realtime_fused output.

    Reads the realtime_fused stage, applies NegCorr according to the
    feature flag, and writes to realtime_negcorr_corrected stage.
    """
    run_id = ctx.run_id
    target_date = ctx.target_date
    mode = _negcorr_mode(ctx.config)
    conn = ctx.db_mgr.new_connection()
    try:
        fused = _read_fused(conn, run_id, target_date)
        if not fused:
            msg = "SKIPPED: no realtime_fused input for NegCorr correction."
            ctx.recorder.record(run_id, target_date, "realtime", STEP_NAME,
                                STEP_ORDER, StepStatus.SKIPPED.value,
                                input_count=0, output_count=0, message=msg)
            return CircuitStepResult(STEP_NAME, StepStatus.SKIPPED, msg,
                                     input_count=0, output_count=0)

        if mode == "off":
            # Passthrough: copy fusion values to negcorr_corrected unchanged
            return _passthrough(ctx, conn, fused, mode)

        # mode == "shadow" or "production": attempt NegCorr correction
        try:
            result_rows, correction_map = _apply_negcorr(ctx, conn, fused, mode)
        except Exception as exc:
            logger.exception("[negcorr_chain] NegCorr computation failed, "
                             "fail-closed to passthrough: %s", exc)
            return _passthrough(ctx, conn, fused, f"FAIL_CLOSED({mode})")

        if mode == "production":
            # Write corrected values as the output
            out_rows = result_rows
            target_stage = CircuitStage.REALTIME_NEGCORR_CORRECTED
            msg_suffix = "production mode: NegCorr correction applied"
        else:
            # shadow mode: write uncorrected fusion values to negcorr_corrected
            # but log corrections separately
            out_rows = _fusion_rows(fused)
            target_stage = CircuitStage.REALTIME_NEGCORR_CORRECTED
            # Also write the shadow copy
            _write_shadow_log(ctx, run_id, target_date, fused, correction_map)
            msg_suffix = "shadow mode: output unchanged, corrections logged"

        out_ids = write_stage_predictions(
            conn, run_id, target_date, CircuitTask.REALTIME,
            target_stage, out_rows,
            source_step=STEP_NAME, is_final_candidate=True)

        n_corrected = sum(1 for v in correction_map.values() if v is not None)
        fused_map = {hb: pid for pid, hb, _ in fused}
        for row, oid in zip(out_rows, out_ids):
            hb = int(row["hour_business"])
            insert_lineage_edge(conn, run_id, target_date, "negcorr",
                                fused_map.get(hb), oid,
                                {"mode": mode,
                                 "corrected": correction_map.get(hb) is not None})

        msg = (f"NegCorr correction complete (mode={mode}): "
               f"{len(out_ids)} hours ({n_corrected} corrected), {msg_suffix}")
        ctx.recorder.record(run_id, target_date, "realtime", STEP_NAME,
                            STEP_ORDER, StepStatus.COMPLETE.value,
                            input_count=len(fused), output_count=len(out_ids),
                            message=msg,
                            metrics_json={"mode": mode,
                                          "corrected": n_corrected,
                                          "n_hours": len(out_ids)})
        return CircuitStepResult(
            STEP_NAME, StepStatus.COMPLETE, msg,
            input_count=len(fused), output_count=len(out_ids),
            artifacts={"mode": mode, "corrected": n_corrected})

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[negcorr_chain] failed")
        ctx.recorder.record(run_id, target_date, "realtime", STEP_NAME,
                            STEP_ORDER, StepStatus.FAIL.value,
                            message=f"exception: {exc}")
        return CircuitStepResult(STEP_NAME, StepStatus.FAIL, str(exc))
    finally:
        conn.close()


def _passthrough(
    ctx: Any, conn: Any, fused: list[tuple], mode: str,
) -> CircuitStepResult:
    """Copy fused values to negcorr_corrected unchanged."""
    run_id = ctx.run_id
    target_date = ctx.target_date
    out_rows = _fusion_rows(fused)
    out_ids = write_stage_predictions(
        conn, run_id, target_date, CircuitTask.REALTIME,
        CircuitStage.REALTIME_NEGCORR_CORRECTED, out_rows,
        source_step=STEP_NAME, is_final_candidate=True)

    fused_map = {hb: pid for pid, hb, _ in fused}
    for row, oid in zip(out_rows, out_ids):
        insert_lineage_edge(conn, run_id, target_date, "negcorr_passthrough",
                            fused_map.get(int(row["hour_business"])), oid,
                            {"mode": mode})

    msg = (f"NegCorr passthrough (mode={mode}): {len(out_ids)} hours, "
           f"fusion values copied to realtime_negcorr_corrected unchanged.")
    ctx.recorder.record(run_id, target_date, "realtime", STEP_NAME,
                        STEP_ORDER, StepStatus.COMPLETE.value,
                        input_count=len(fused), output_count=len(out_ids),
                        message=msg, metrics_json={"mode": mode,
                                                    "corrected": 0,
                                                    "n_hours": len(out_ids)})
    return CircuitStepResult(
        STEP_NAME, StepStatus.COMPLETE, msg,
        input_count=len(fused), output_count=len(out_ids),
        artifacts={"mode": mode, "corrected": 0})


def _fusion_rows(fused: list[tuple]) -> list[dict[str, Any]]:
    """Convert fused (id, hb, price) rows to standard output rows."""
    rows: list[dict[str, Any]] = []
    for _, hb, price in fused:
        rows.append({
            "hour_business": hb,
            "pred_price": price,
            "model_name": "rt_negcorr_output",
            "model_version": "negcorr_v1",
            "is_shadow": False,
            "is_selected": False,
            "selected_reason": "negcorr: fusion passthrough",
            "quality_flags": ["negcorr_passthrough"],
        })
    return rows


def _apply_negcorr(
    ctx: Any, conn: Any, fused: list[tuple], mode: str,
) -> tuple[list[dict[str, Any]], dict[int, Any]]:
    """Apply NegCorr correction using NegCorrShadowModule.

    Returns:
        (corrected_rows, correction_map)
        correction_map: {hb: corrected_price_or_None} for audit.
    """
    target_date = ctx.target_date
    run_id = ctx.run_id

    # Build input data for NegCorr module
    fused_map = {hb: price for _, hb, price in fused}
    hours = list(range(1, 25))
    fused_prices = [fused_map.get(h, 0.0) for h in hours]

    da_map = _read_da_anchor(conn, target_date)
    da_prices = [da_map.get(h, 0.0) for h in hours]

    import pandas as pd  # type: ignore[import-untyped]

    fused_series = pd.Series(fused_prices, index=hours)
    da_series = pd.Series(da_prices, index=hours)
    hour_series = pd.Series(hours, index=hours)

    # Initialize NegCorr module
    from fusion.correction.negcorr_shadow import NegCorrShadowModule

    module = NegCorrShadowModule()

    if not module.is_available():
        raise RuntimeError("NegCorrShadowModule not available")

    # Run prediction.
    # NOTE: NegCorrShadowModule.predict() has its first parameter named
    # ``a05_prediction`` for historical reasons (the V5 research prototype
    # operated on A05 input). In the V3.1 production pipeline, NegCorr
    # operates on the FUSED multi-candidate output (not A05 alone) as
    # specified by the integration design. The rename is intentional and
    # the module handles any input series correctly.
    corrected_series = module.predict(fused_series, da_series, hour_series)

    # Build output rows
    out_rows: list[dict[str, Any]] = []
    correction_map: dict[int, Any] = {}
    for hb in hours:
        orig = fused_map.get(hb, 0.0)
        corr = float(corrected_series.get(hb, orig))
        if abs(corr - orig) > 1e-6:
            correction_map[hb] = corr
        else:
            correction_map[hb] = None

        # Record repair decision for each corrected hour
        if correction_map[hb] is not None:
            insert_repair_decision(conn, RepairDecision(
                run_id=run_id, target_date=target_date, task=CircuitTask.REALTIME,
                hour_business=hb, repair_stage=RepairStage.NEGCORR,
                rule_name=f"negcorr_{mode}", before_value=orig, after_value=corr,
                severity="warning" if mode == "production" else "info",
                reason=f"NegCorr {mode}: {orig:.2f} -> {corr:.2f}"))

        out_rows.append({
            "hour_business": hb,
            "pred_price": corr,
            "model_name": "rt_negcorr_output",
            "model_version": "negcorr_v1",
            "is_shadow": (mode == "shadow"),
            "is_selected": False,
            "selected_reason": f"negcorr_{mode}: {'corrected' if correction_map[hb] is not None else 'unchanged'}",
            "quality_flags": ["negcorr_corrected" if correction_map[hb] is not None
                              else "negcorr_unchanged"],
        })

    return out_rows, correction_map


def _write_shadow_log(
    ctx: Any, run_id: str, target_date: str,
    fused: list[tuple], correction_map: dict[int, Any],
) -> None:
    """Write NegCorr shadow log entry (for audit/review).

    Records the comparison between original fusion values and NegCorr
    corrected values for all 24 hours — this is the "what-if" record.
    """
    try:
        import os
        from pathlib import Path

        log_dir = Path(ctx.config.get("negcorr_shadow_log_dir", "logs/shadow"))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"negcorr_shadow_{target_date}.jsonl"

        fused_map = {hb: price for _, hb, price in fused}
        with open(log_path, "a", encoding="utf-8") as f:
            for hb in sorted(fused_map):
                negcorr_val = correction_map.get(hb)
                # correction_map provides the corrected value, or None for
                # unchanged hours (in which case the NegCorr prediction equals
                # the fusion value).
                if negcorr_val is None:
                    negcorr_val = fused_map[hb]
                entry = {
                    "run_id": run_id,
                    "business_day": target_date,
                    "hour_business": hb,
                    "fusion_pred": fused_map[hb],
                    "negcorr_pred": negcorr_val,
                    "delta": negcorr_val - fused_map[hb],
                    "variant": "shadow",
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("[negcorr_chain] failed to write shadow log: %s", exc)
