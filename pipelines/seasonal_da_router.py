"""
seasonal_da_router.py — Seasonal DA Policy Router for EFM3 3.0.

Switches between two prediction sources based on season:
  - Winter (Nov-Feb):  Use da_anchor predictions directly.
  - Non-winter:        Use official_baseline (realtime) predictions,
                       with a fallback to sgdfnet raw model.

Writes 24 final-selected decisions (hour_business 1..24) via PredictionStore.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from pipelines.fusion_shadow_v1 import WINTER_MONTHS

logger = logging.getLogger(__name__)

# Season constants (month numbers, 1-based)
_WINTER_MONTH_NUMS = {11, 12, 1, 2}

POLICY_NAME = "seasonal_da_router"


def _is_winter(target_date: str) -> bool:
    """Return True if *target_date* falls in a winter month (Nov-Feb)."""
    month = datetime.strptime(target_date, "%Y-%m-%d").month
    return month in _WINTER_MONTH_NUMS


def _build_hour_map(
    predictions: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Index a prediction list by *hour_business* for O(1) lookups."""
    return {int(p["hour_business"]): p for p in predictions}


def run_seasonal_da_router(
    target_date: str,
    prediction_store: Any,
    run_id: str,
) -> dict[str, Any]:
    """Execute the seasonal DA router policy for *target_date*.

    Parameters
    ----------
    target_date:
        Target business day in ``YYYY-MM-DD`` format.
    prediction_store:
        A ``PredictionStore`` instance (from ``common.prediction_store``).
    run_id:
        Identifier of the current orchestration run.

    Returns
    -------
    dict
        Status summary with keys ``status``, ``target_date``, ``policy``,
        ``selected_model``, ``hours_decided``, ``hours_missing``.
    """
    decisions: list[dict[str, Any]] = []
    hours_missing = 0
    selected_model: Optional[str] = None

    # ------------------------------------------------------------------
    # 1. Determine season and read the appropriate source predictions
    # ------------------------------------------------------------------
    is_winter = _is_winter(target_date)
    logger.info(
        "[SeasonalDARouter] target_date=%s is_winter=%s",
        target_date,
        is_winter,
    )

    if is_winter:
        # ── Winter: use da_anchor predictions ──────────────────────
        raw_preds = prediction_store.read_predictions(
            run_id=run_id,
            target_date=target_date,
            stage="da_anchor",
        )
        source_label = "da_anchor"
        decision_reason = "winter_da_anchor_policy"
    else:
        # ── Non-winter: use official_baseline, fallback sgdfnet, then da_anchor ────
        raw_preds = prediction_store.read_predictions(
            run_id=run_id,
            target_date=target_date,
            stage="official_baseline",
            task="realtime",
        )
        if not raw_preds:
            logger.warning(
                "[SeasonalDARouter] official_baseline not available for %s — "
                "falling back to sgdfnet raw model",
                target_date,
            )
            all_raw = prediction_store.read_predictions(
                run_id=run_id,
                target_date=target_date,
                stage="raw_model",
            )
            raw_preds = [p for p in all_raw if p.get("model_name") == "sgdfnet"]
            if raw_preds:
                source_label = "sgdfnet"
                decision_reason = "non_winter_official_baseline_fallback_sgdfnet"
            else:
                # Final fallback: day-ahead anchor (day-ahead clearing price).
                # In a backfilled historical simulation there is no realtime
                # baseline, so the day-ahead clearing price serves as the
                # benchmark "forecast" against the real-time actual.
                da_preds = prediction_store.read_predictions(
                    run_id=run_id,
                    target_date=target_date,
                    stage="da_anchor",
                )
                if da_preds:
                    raw_preds = da_preds
                    source_label = "da_anchor"
                    decision_reason = "non_winter_da_anchor_fallback"
                    logger.info(
                        "[SeasonalDARouter] non-winter falling back to da_anchor "
                        "for %s (%d hours)", target_date, len(da_preds),
                    )
                else:
                    source_label = "none"
                    decision_reason = "non_winter_no_source"
        else:
            source_label = "official_baseline"
            decision_reason = "non_winter_official_baseline"

    selected_model = source_label  # primary source name (for summary)

    # ------------------------------------------------------------------
    # 2. Index predictions by hour_business
    # ------------------------------------------------------------------
    hour_map = _build_hour_map(raw_preds)

    # ------------------------------------------------------------------
    # 3. Build one decision per hour (1..24)
    # ------------------------------------------------------------------
    for hb in range(1, 25):
        pred = hour_map.get(hb)

        if pred is None:
            logger.warning(
                "[SeasonalDARouter] No prediction for hour_business=%d on %s "
                "(source=%s)",
                hb,
                target_date,
                selected_model,
            )
            hours_missing += 1
            continue

        try:
            pred_price = float(pred["pred_price"])
        except (ValueError, TypeError, KeyError):
            logger.warning(
                "[SeasonalDARouter] Invalid or missing pred_price for "
                "hour_business=%d on %s (source=%s)",
                hb,
                target_date,
                selected_model,
            )
            hours_missing += 1
            continue

        decision: dict[str, Any] = {
            "hour_business": hb,
            "pred_price": pred_price,
            "policy_name": POLICY_NAME,
            "selected_model": selected_model,
            "decision_reason": decision_reason,
        }
        decisions.append(decision)

    # ------------------------------------------------------------------
    # 4. Persist decisions
    # ------------------------------------------------------------------
    if decisions:
        written = prediction_store.write_selected_final(
            run_id=run_id,
            target_date=target_date,
            decisions=decisions,
        )
        logger.info(
            "[SeasonalDARouter] Wrote %d / %d decisions for %s",
            written,
            len(decisions),
            target_date,
        )
    else:
        logger.error(
            "[SeasonalDARouter] Zero decisions produced for %s — "
            "no predictions available from any source",
            target_date,
        )

    # ------------------------------------------------------------------
    # 5. Determine overall status
    # ------------------------------------------------------------------
    total_hours = 24
    decided = len(decisions)

    if decided == total_hours:
        status = "ok"
    elif decided > 0:
        status = "partial"
    else:
        status = "failed"

    return {
        "status": status,
        "target_date": target_date,
        "policy": POLICY_NAME,
        "selected_model": selected_model or "none",
        "hours_decided": decided,
        "hours_missing": hours_missing,
    }
