#!/usr/bin/env python
"""
Compute BGEW (Bounded Generalized Exponentiated Weighting) fusion weights
from the DB ledger for use in the production circuit.

Usage:
  weights = compute_bgew_weights(db_url, task='dayahead', lookback_days=30)
  # Returns: {period: {model_name: weight}}
  # cycle all 3 periods and both tasks for the full set
"""
from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.db.connection import DbConnectionManager

logger = logging.getLogger(__name__)

# ── BGEW algorithm (ported from 2.5 DailyLedgerGEF) ──────────────

SMAFE_FLOOR = 50
PERIODS = ["1_8", "9_16", "17_24"]
DEFAULT_CONFIG = {
    "eta": 0.8,
    "weight_floor": 0.03,
    "window_days": 30,
    "day_gate_weights": [0.7, 0.3],
    "composite_alpha": 0.7,
}


def smape_floor50(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    mask = ~(np.isnan(yt) | np.isnan(yp))
    if mask.sum() == 0:
        return np.nan
    yt = yt[mask]
    yp = yp[mask]
    yt_c = np.maximum(yt, SMAFE_FLOOR)
    yp_c = np.maximum(yp, SMAFE_FLOOR)
    denom = (np.abs(yp_c) + np.abs(yt_c)) / 2.0
    denom = np.maximum(denom, 1e-10)
    return float(np.mean(np.abs(yp_c - yt_c) / denom) * 100.0)


def compute_bgew_weights(
    db_url: str,
    task: str = "dayahead",  # 'dayahead' or 'realtime'
    lookback_days: int = 30,
    eta: float = 0.8,
    weight_floor: float = 0.03,
) -> dict:
    """
    Compute BGEW fusion weights from historical ledger data.

    Returns: dict mapping period -> {model_name: weight}
    """
    url = db_url.replace("%%23", "%23")
    mgr = DbConnectionManager(db_url=url)
    conn = mgr.new_connection()
    cur = conn.cursor()

    # Determine prediction stage and actual column
    if task == "dayahead":
        pred_stage = "dayahead_raw_model"
        actual_col = "da_anchor"
        actual_table = "efm_actual_prices"
    else:
        pred_stage = "realtime_raw_model"
        actual_col = "rt_actual"
        actual_table = "efm_actual_prices"

    # Fetch raw model predictions + actuals for the last lookback_days.
    # 3NF: target_date lives on efm_runs; model/stage are FKs to dim tables.
    cur.execute(f"""
        SELECT r.target_date, p.hour_business, m.name AS model_name, p.pred_price,
               a.{actual_col}
        FROM efm_predictions p
        JOIN efm_runs r ON p.run_id = r.run_id
        JOIN efm_dim_model m ON p.model_id = m.id
        JOIN efm_dim_stage s ON p.stage_id = s.id
        JOIN {actual_table} a
          ON r.target_date = a.target_date AND p.hour_business = a.hour_business
        WHERE s.name = %s
          AND p.task = %s
          AND a.{actual_col} IS NOT NULL
          AND r.target_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        ORDER BY r.target_date, p.hour_business, m.name
    """, (pred_stage, task, lookback_days * 2))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        logger.warning(f"No training data found for task={task}")
        return {p: {} for p in PERIODS}

    # Build DataFrame
    df = pd.DataFrame(rows, columns=["target_date", "hour_business", "model_name", "pred_price", "y_true"])
    df["target_date"] = pd.to_datetime(df["target_date"])

    # Classify period
    def _period(hb):
        if 1 <= hb <= 8:
            return "1_8"
        elif 9 <= hb <= 16:
            return "9_16"
        return "17_24"

    df["period"] = df["hour_business"].apply(_period)
    models = sorted(df["model_name"].unique())

    if len(models) < 1:
        return {p: {} for p in PERIODS}

    # BGEW for each period
    period_weights = {}
    for period in PERIODS:
        pdf = df[df["period"] == period].copy()
        if pdf.empty:
            period_weights[period] = {}
            continue

        # Get unique days, sorted (oldest first)
        days = sorted(pdf["target_date"].unique())
        if len(days) > lookback_days:
            days = days[-lookback_days:]

        # Start with equal weights
        w = {m: 1.0 / len(models) for m in models}

        # Process days from OLDEST to NEWEST
        for day_idx, day in enumerate(days):
            day_df = pdf[pdf["target_date"] == day]

            # Per-model loss
            losses = {}
            for model in models:
                mdf = day_df[day_df["model_name"] == model]
                if mdf.empty or mdf["y_true"].isna().any():
                    continue
                yt = mdf["y_true"].values
                yp = mdf["pred_price"].values
                losses[model] = smape_floor50(yt, yp)

            if not losses:
                continue

            # Normalize by median
            loss_vals = np.array(list(losses.values()))
            med_loss = np.median(loss_vals)
            if med_loss < 1e-10:
                med_loss = 1.0

            # Day gate: recent days get more weight
            age_days = len(days) - day_idx
            day_gate = 0.7 if age_days <= 15 else 0.3

            # Update: w_m *= exp(-eta * day_gate * normalized_loss_m)
            for model in models:
                if model in losses:
                    norm_loss = losses[model] / med_loss
                    w[model] *= np.exp(-eta * day_gate * norm_loss)
                    w[model] = max(w[model], weight_floor)

            # Renormalize
            total = sum(w.values())
            if total > 0:
                for model in w:
                    w[model] /= total

        period_weights[period] = {
            m: round(w[m], 6) for m in models if w[m] > 0
        }

    return period_weights


def get_model_level_weights(
    period_weights: dict,
) -> dict[str, float]:
    """
    Convert per-period weights to model-level weights (averaged across periods).
    """
    model_weights: dict[str, list[float]] = defaultdict(list)
    for period, pw in period_weights.items():
        for model, w in pw.items():
            model_weights[model].append(w)
    return {m: round(np.mean(v), 4) for m, v in model_weights.items()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db_url = os.environ.get("EFM3_DB_URL",
                            "mysql+pymysql://root:Zlt20060313%%23@127.0.0.1:3306/efm3")
    url = db_url.replace("%%23", "%23")

    for task in ["dayahead", "realtime"]:
        print(f"\n[{task.upper()}] BGEW weights:")
        pw = compute_bgew_weights(url, task=task, lookback_days=30)
        for period in PERIODS:
            if pw.get(period):
                weights_str = ", ".join(f"{m}={w*100:.1f}%" for m, w in pw[period].items())
                print(f"  {period}: {weights_str}")
        ml = get_model_level_weights(pw)
        if ml:
            avg_str = ", ".join(f"{m}={w*100:.1f}%" for m, w in ml.items())
            print(f"  avg: {avg_str}")
