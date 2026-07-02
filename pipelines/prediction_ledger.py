"""
Prediction ledger & actual ledger management.

Core abstraction: a cross-date accumulation of model predictions
and actual prices, keyed by (task, model_name, forecast_date,
target_day, business_day, hour_business).

The prediction ledger is the single source of truth for the
30-day rolling window used by the daily weight learner.

KEY RULE: hour 24 = D+1 00:00, so business_day + hour_business
is the canonical key, NOT target_day + hour_business alone.

No validation tap / rolling OOF / online validation here.
Just honest predictions + actuals accumulated day by day.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

import numpy as np

from utils.business_day import (
    PREDICTION_LEDGER_COLUMNS,
    ACTUAL_LEDGER_COLUMNS,
    TRAINING_TABLE_COLUMNS,
    business_day_from_timestamp,
    hour_business_from_timestamp,
    infer_period,
)

logger = logging.getLogger(__name__)

# Unique key columns for dedup
# Must include business_day because hour 24 = D+1 00:00
PREDICTION_UNIQUE_KEY = ["task", "model_name", "forecast_date", "target_day", "business_day", "hour_business"]
ACTUAL_UNIQUE_KEY = ["task", "target_day", "business_day", "hour_business"]


# ===========================================================================
# Prediction ledger read/write
# ===========================================================================

def _ensure_ledger_dir(ledger_root: Path, task: str) -> Path:
    """Ensure prediction ledger directory exists and return path."""
    d = ledger_root / task / "prediction"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_actual_dir(ledger_root: Path, task: str) -> Path:
    """Ensure actual ledger directory exists and return path."""
    d = ledger_root / task / "actual"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_predictions_to_ledger(
    df: pd.DataFrame,
    ledger_root: Path,
    task: str,
    source_file: str = "",
) -> dict:
    """
    Append prediction rows to the prediction ledger.

    Dedup rule: same (task, model_name, forecast_date, target_day,
    hour_business) → keep latest run_id.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain at minimum: task, model_name, forecast_date,
        target_day, business_day, ds, hour_business, period, y_pred.
    ledger_root : Path
        Root of ledger directory (e.g. outputs/ledger).
    task : str
        "dayahead" or "realtime".
    source_file : str
        For provenance tracking.

    Returns
    -------
    dict with keys: status, parquet_path, csv_path, rows_before, rows_after,
    duplicates_removed, duplicate_report
    """
    ledger_dir = _ensure_ledger_dir(ledger_root, task)
    pq_path = ledger_dir / "prediction_ledger.parquet"
    csv_path = ledger_dir / "prediction_ledger.csv"

    # Prepare DataFrame
    df = df.copy()
    df["created_at"] = datetime.now(timezone.utc).isoformat()
    if "source_file" not in df.columns:
        df["source_file"] = source_file

    # Ensure all required columns exist
    for col in PREDICTION_LEDGER_COLUMNS:
        if col not in df.columns:
            df[col] = None

    new_rows = len(df)

    # Load existing ledger
    existing = None
    rows_before = 0
    if pq_path.exists():
        existing = pd.read_parquet(pq_path)
        rows_before = len(existing)

    # Combine and dedup
    if existing is not None and len(existing) > 0:
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df

    # Dedup: keep last (most recent) by key
    key_cols = [c for c in PREDICTION_UNIQUE_KEY if c in combined.columns]
    before_dedup = len(combined)
    combined = combined.sort_values("created_at", ascending=True)
    combined = combined.drop_duplicates(subset=key_cols, keep="last")
    after_dedup = len(combined)
    duplicates_removed = before_dedup - after_dedup

    # Build duplicate report
    duplicate_report = []
    if duplicates_removed > 0:
        # Find which keys were duplicated
        dup_mask = combined.duplicated(subset=key_cols, keep=False)
        if dup_mask.any():
            # This is approximate since we already deduped
            pass
        duplicate_report.append({
            "duplicates_removed": int(duplicates_removed),
            "new_rows": int(new_rows),
            "rows_before": int(rows_before),
            "rows_after": int(after_dedup),
        })

    # Select and order columns
    out_cols = [c for c in PREDICTION_LEDGER_COLUMNS if c in combined.columns]
    combined = combined[out_cols]

    # Write
    combined.to_parquet(pq_path, index=False)
    combined.to_csv(csv_path, index=False)

    logger.info(
        f"Prediction ledger [{task}]: {rows_before} → {after_dedup} rows "
        f"(+{new_rows} new, -{duplicates_removed} duplicates)"
    )

    return {
        "status": "ok",
        "parquet_path": str(pq_path),
        "csv_path": str(csv_path),
        "rows_before": rows_before,
        "rows_after": after_dedup,
        "new_rows": new_rows,
        "duplicates_removed": duplicates_removed,
        "duplicate_report": duplicate_report,
    }


def load_prediction_ledger(
    ledger_root: Path,
    task: str,
    business_days: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Load prediction ledger, optionally filtered to a range of business days.

    Parameters
    ----------
    ledger_root : Path
        Root of ledger directory.
    task : str
        "dayahead" or "realtime".
    business_days : list[str], optional
        If provided, filter to these business days (inclusive).

    Returns
    -------
    pd.DataFrame (empty if no ledger exists)
    """
    pq_path = ledger_root / task / "prediction" / "prediction_ledger.parquet"
    if not pq_path.exists():
        logger.warning(f"Prediction ledger not found: {pq_path}")
        return pd.DataFrame(columns=PREDICTION_LEDGER_COLUMNS)

    df = pd.read_parquet(pq_path)
    if business_days:
        if "business_day" in df.columns:
            df = df[df["business_day"].isin(business_days)]
        elif "target_day" in df.columns:
            df = df[df["target_day"].isin(business_days)]

    return df


# ===========================================================================
# Actual ledger read/write
# ===========================================================================

def update_actual_ledger(
    df: pd.DataFrame,
    ledger_root: Path,
    task: str,
    source_file: str = "",
) -> dict:
    """
    Update the actual ledger with ground-truth prices.

    Dedup rule: same (task, target_day, hour_business) → keep latest.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain: task, target_day, business_day, ds, hour_business,
        period, y_true.
    ledger_root : Path
        Root of ledger directory.
    task : str
        "dayahead" or "realtime".
    source_file : str

    Returns
    -------
    dict with status info.
    """
    actual_dir = _ensure_actual_dir(ledger_root, task)
    pq_path = actual_dir / "actual_ledger.parquet"
    csv_path = actual_dir / "actual_ledger.csv"

    df = df.copy()
    df["actual_available_at"] = datetime.now(timezone.utc).isoformat()
    if "source_file" not in df.columns:
        df["source_file"] = source_file

    for col in ACTUAL_LEDGER_COLUMNS:
        if col not in df.columns:
            df[col] = None

    new_rows = len(df)

    existing = None
    rows_before = 0
    if pq_path.exists():
        existing = pd.read_parquet(pq_path)
        rows_before = len(existing)

    if existing is not None and len(existing) > 0:
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df

    key_cols = [c for c in ACTUAL_UNIQUE_KEY if c in combined.columns]
    before_dedup = len(combined)
    combined = combined.sort_values("actual_available_at", ascending=True)
    combined = combined.drop_duplicates(subset=key_cols, keep="last")
    after_dedup = len(combined)

    out_cols = [c for c in ACTUAL_LEDGER_COLUMNS if c in combined.columns]
    combined = combined[out_cols]

    combined.to_parquet(pq_path, index=False)
    combined.to_csv(csv_path, index=False)

    logger.info(
        f"Actual ledger [{task}]: {rows_before} → {after_dedup} rows "
        f"(+{new_rows} new, -{before_dedup - after_dedup} duplicates)"
    )

    return {
        "status": "ok",
        "parquet_path": str(pq_path),
        "csv_path": str(csv_path),
        "rows_before": rows_before,
        "rows_after": after_dedup,
    }


def load_actual_ledger(
    ledger_root: Path,
    task: str,
    business_days: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Load actual ledger, optionally filtered by business days."""
    pq_path = ledger_root / task / "actual" / "actual_ledger.parquet"
    if not pq_path.exists():
        logger.warning(f"Actual ledger not found: {pq_path}")
        return pd.DataFrame(columns=ACTUAL_LEDGER_COLUMNS)

    df = pd.read_parquet(pq_path)
    if business_days:
        if "business_day" in df.columns:
            df = df[df["business_day"].isin(business_days)]
        elif "target_day" in df.columns:
            df = df[df["target_day"].isin(business_days)]

    return df


# ===========================================================================
# Ledger training table construction
# ===========================================================================

def build_ledger_training_table(
    prediction_ledger: pd.DataFrame,
    actual_ledger: pd.DataFrame,
    target_day: str,
    window_days: int = 30,
    day_gate_recent: float = 0.7,
    day_gate_oldest: float = 0.3,
    recent_week_boost: bool = True,
    recent_week_max_gate: float = 0.85,
    window_days_list: list[str] | None = None,
) -> pd.DataFrame:
    """
    Build the training table for weight learning on target_day.

    Joins prediction ledger with actual ledger for the specified window.

    Parameters
    ----------
    prediction_ledger : pd.DataFrame
        All prediction rows from the ledger.
    actual_ledger : pd.DataFrame
        All actual price rows from the ledger.
    target_day : str
        The prediction day D (YYYY-MM-DD).
    window_days : int
        Number of days in the lookback window (default 30).
        Used only when *window_days_list* is None.
    day_gate_recent : float
        Base weight for the most recent day (D-1).
    day_gate_oldest : float
        Base weight for the oldest day (D-30).
    recent_week_boost : bool
        Enable smooth recent-week boost on top of linear day_gate.
    recent_week_max_gate : float
        Max day_gate with boost enabled (default 0.85).
    window_days_list : list[str], optional
        Explicit list of training days (YYYY-MM-DD, newest-first).
        When provided, overrides *window_days* and supports non-contiguous
        date selections produced by adaptive training-day selection.

    Returns
    -------
    pd.DataFrame with columns: task, model_name, target_day, business_day,
    ds, hour_business, period, y_pred, y_true, age_days, day_gate.
    """
    D = pd.Timestamp(target_day)

    # Use explicit list if provided, otherwise generate contiguous window
    if window_days_list is None:
        window_days_list = []
        for i in range(1, window_days + 1):
            d = D - pd.Timedelta(days=i)
            window_days_list.append(d.strftime("%Y-%m-%d"))

    # Filter prediction ledger to window
    pred = prediction_ledger.copy()
    if "target_day" in pred.columns:
        pred = pred[pred["target_day"].isin(window_days_list)]

    # Filter actual ledger to window
    act = actual_ledger.copy()
    if "target_day" in act.columns:
        act = act[act["target_day"].isin(window_days_list)]

    # Merge predictions with actuals — use business_day + hour_business
    merge_keys = ["task", "business_day", "hour_business"]
    merge_keys = [k for k in merge_keys if k in pred.columns and k in act.columns]

    if not merge_keys:
        # Fallback: try target_day + hour_business
        merge_keys = ["task", "target_day", "hour_business"]
        merge_keys = [k for k in merge_keys if k in pred.columns and k in act.columns]

    # Keep only needed columns from actual
    act_cols = merge_keys + ["y_true"]
    act_cols = [c for c in act_cols if c in act.columns]
    act_sub = act[act_cols].copy()

    # Merge
    training = pred.merge(act_sub, on=merge_keys, how="inner", suffixes=("", "_actual"))

    # Compute age_days: D-1 → 1, D-30 → 30
    if "target_day" in training.columns:
        training["_target_dt"] = pd.to_datetime(training["target_day"])
        training["age_days"] = (D - training["_target_dt"]).dt.days
        training = training.drop(columns=["_target_dt"])
    else:
        training["age_days"] = 0

    # Compute day_gate: linear decay from day_gate_recent to day_gate_oldest
    n_days = max(len(window_days_list), 2)
    decay_rate = (day_gate_recent - day_gate_oldest) / (n_days - 1)
    training["day_gate"] = day_gate_recent - (training["age_days"] - 1) * decay_rate

    # Recent week boost: smooth enhancement for D-1 through D-7
    if recent_week_boost:
        age = training["age_days"].values.astype(float)
        weekly_boost = 0.15 * np.maximum(0, (8.0 - age) / 7.0)
        training["day_gate"] = training["day_gate"] + weekly_boost
        training["day_gate"] = training["day_gate"].clip(day_gate_oldest, recent_week_max_gate)

    # Select and order columns
    out_cols = [c for c in TRAINING_TABLE_COLUMNS if c in training.columns]
    training = training[out_cols].reset_index(drop=True)

    logger.info(
        f"Training table: {len(training)} rows, "
        f"day_gate range [{training['day_gate'].min():.3f}, {training['day_gate'].max():.3f}]"
    )

    return training


# ===========================================================================
# Coverage utilities
# ===========================================================================

def check_ledger_coverage(
    prediction_ledger: pd.DataFrame,
    actual_ledger: pd.DataFrame,
    task: str,
    window_days: list[str],
    expected_models: list[str],
) -> pd.DataFrame:
    """
    Check coverage of prediction + actual ledger for a set of window days.

    Returns a DataFrame with per-day, per-model coverage stats.
    expected = 24 rows per model per day.
    """
    records = []
    for day in window_days:
        pred_day = prediction_ledger[
            (prediction_ledger["target_day"] == day)
            & (prediction_ledger["task"] == task)
        ]
        act_day = actual_ledger[
            (actual_ledger["target_day"] == day)
            & (actual_ledger["task"] == task)
        ]

        for model in expected_models:
            model_pred = pred_day[pred_day["model_name"] == model]
            n_pred = len(model_pred)
            n_expected = 24  # 24 hours per model per day
            n_actual = len(act_day)  # actual rows for this day

            coverage_pct = round(n_pred / n_expected * 100, 1) if n_expected > 0 else 0
            has_actual = n_actual >= 24
            status = "ok" if n_pred == n_expected and has_actual else "incomplete"

            records.append({
                "target_day": day,
                "task": task,
                "model_name": model,
                "n_pred": n_pred,
                "n_actual": n_actual,
                "n_expected": n_expected,
                "coverage_pct": coverage_pct,
                "status": status,
            })

    return pd.DataFrame(records)


def dedupe_ledger(
    df: pd.DataFrame,
    key_cols: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Deduplicate a ledger DataFrame by unique key, keeping latest."""
    if key_cols is None:
        key_cols = PREDICTION_UNIQUE_KEY
    key_cols = [c for c in key_cols if c in df.columns]

    if "created_at" in df.columns:
        df = df.sort_values("created_at", ascending=True)
    elif "actual_available_at" in df.columns:
        df = df.sort_values("actual_available_at", ascending=True)

    before = len(df)
    df = df.drop_duplicates(subset=key_cols, keep="last")
    after = len(df)
    if before != after:
        logger.info(f"Dedup: {before} → {after} rows ({before - after} removed)")

    return df
