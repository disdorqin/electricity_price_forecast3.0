"""
Delivery quality checks — ledger window validation, submission validation,
next-day readiness.

All checks are pure validation: no GPU, no model inference, no ledger writes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

SUBMISSION_COLUMNS = [
    "business_day", "ds", "hour_business", "period",
    "dayahead_price", "realtime_price",
]

# ---------------------------------------------------------------------------
# Expected grid builder
# ---------------------------------------------------------------------------


def build_expected_ledger_grid(start_date: str, days: int, task: str) -> pd.DataFrame:
    """Build the full expected row grid for a task's ledger window.

    Parameters
    ----------
    start_date : str
        The target date (D). Window is D-30 .. D-1.
    days : int
        Number of days in the window (expected 30).
    task : str
        ``"dayahead"`` or ``"realtime"``.

    Returns
    -------
    pd.DataFrame with columns [business_day, model_name, hour_business]
    representing every row that must exist.
    """
    if task == "dayahead":
        models = ["lightgbm", "timesfm", "timemixer"]
    else:
        models = ["timesfm", "sgdfnet", "timemixer", "rt916"]

    start_dt = pd.Timestamp(start_date)
    window_end = start_dt - pd.Timedelta(days=1)
    window_start = start_dt - pd.Timedelta(days=days)

    date_range = pd.date_range(start=window_start, end=window_end, freq="D")
    rows = []
    for d in date_range:
        d_str = d.strftime("%Y-%m-%d")
        for model in models:
            for h in range(1, 25):
                rows.append({
                    "business_day": d_str,
                    "model_name": model,
                    "hour_business": h,
                })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Ledger window validation  (section 3 in design)
# ---------------------------------------------------------------------------


def validate_ledger_window(
    target_date: str,
    ledger_root: str | Path,
    days: int = 30,
) -> dict:
    """Strictly validate D-30..D-1 ledger coverage.

    Checks all four ledger files for complete daily coverage:
      - dayahead prediction  (3 models x 24h)
      - realtime prediction  (4 models x 24h)
      - dayahead actual      (24h)
      - realtime actual      (24h)

    Returns a dict with status PASS/FAIL, errors, warnings, and summary.
    """
    ledger_root = Path(ledger_root)
    errors: list[dict] = []
    warnings: list[str] = []

    ledger_paths = {
        "dayahead prediction": ledger_root / "dayahead" / "prediction" / "prediction_ledger.parquet",
        "dayahead actual": ledger_root / "dayahead" / "actual" / "actual_ledger.parquet",
        "realtime prediction": ledger_root / "realtime" / "prediction" / "prediction_ledger.parquet",
        "realtime actual": ledger_root / "realtime" / "actual" / "actual_ledger.parquet",
    }

    # Build expected grid
    da_pred_grid = build_expected_ledger_grid(target_date, days, "dayahead")
    rt_pred_grid = build_expected_ledger_grid(target_date, days, "realtime")
    actual_grid = _build_actual_grid(target_date, days)

    # Dayahead prediction
    _check_prediction_ledger(
        ledger_paths["dayahead prediction"],
        "dayahead prediction",
        da_pred_grid,
        errors,
    )

    # Realtime prediction
    _check_prediction_ledger(
        ledger_paths["realtime prediction"],
        "realtime prediction",
        rt_pred_grid,
        errors,
    )

    # Dayahead actual
    _check_actual_ledger(
        ledger_paths["dayahead actual"],
        "dayahead actual",
        actual_grid,
        errors,
    )

    # Realtime actual
    _check_actual_ledger(
        ledger_paths["realtime actual"],
        "realtime actual",
        actual_grid,
        errors,
    )

    # Build summary counts
    summary = _build_summary_counts(
        ledger_paths, target_date, days, da_pred_grid, rt_pred_grid,
    )

    result: dict[str, Any] = {
        "status": "PASS" if not errors else "FAIL",
        "target_date": target_date,
        "window_start": (pd.Timestamp(target_date) - pd.Timedelta(days=days)).strftime("%Y-%m-%d"),
        "window_end": (pd.Timestamp(target_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "missing": errors,
    }

    return result


def _build_actual_grid(start_date: str, days: int) -> pd.DataFrame:
    """Build expected grid for actual ledger (no model dimension)."""
    start_dt = pd.Timestamp(start_date)
    window_end = start_dt - pd.Timedelta(days=1)
    window_start = start_dt - pd.Timedelta(days=days)

    date_range = pd.date_range(start=window_start, end=window_end, freq="D")
    rows = []
    for d in date_range:
        d_str = d.strftime("%Y-%m-%d")
        for h in range(1, 25):
            rows.append({"business_day": d_str, "hour_business": h})
    return pd.DataFrame(rows)


def _check_prediction_ledger(
    path: Path,
    label: str,
    expected_grid: pd.DataFrame,
    errors: list,
) -> None:
    """Check prediction ledger against expected grid."""
    if not path.exists():
        errors.append({
            "ledger": label,
            "error": f"file not found: {path}",
        })
        return

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        errors.append({
            "ledger": label,
            "error": f"cannot read parquet: {exc}",
        })
        return

    _check_ledger_against_grid(df, label, expected_grid, errors, is_prediction=True)


def _check_actual_ledger(
    path: Path,
    label: str,
    expected_grid: pd.DataFrame,
    errors: list,
) -> None:
    """Check actual ledger against expected grid."""
    if not path.exists():
        errors.append({
            "ledger": label,
            "error": f"file not found: {path}",
        })
        return

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        errors.append({
            "ledger": label,
            "error": f"cannot read parquet: {exc}",
        })
        return

    _check_ledger_against_grid(df, label, expected_grid, errors, is_prediction=False)


def _check_ledger_against_grid(
    df: pd.DataFrame,
    label: str,
    expected_grid: pd.DataFrame,
    errors: list,
    is_prediction: bool,
) -> None:
    """Compare actual ledger counts against expected grid."""
    # Determine date column
    date_col = "target_day" if "target_day" in df.columns else "business_day"
    if date_col not in df.columns:
        errors.append({
            "ledger": label,
            "error": f"no '{date_col}' column found (columns: {list(df.columns)})",
        })
        return

    # Normalise date column to string
    df = df.copy()
    df["_date_str"] = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")

    # Normalise hour_business to int
    if "hour_business" in df.columns:
        df["hour_business"] = df["hour_business"].astype(int)

    # Count existing rows
    if is_prediction:
        if "model_name" not in df.columns:
            errors.append({
                "ledger": label,
                "error": f"no 'model_name' column in prediction ledger",
            })
            return

        counts = (
            df.groupby(["_date_str", "model_name"])["hour_business"]
            .nunique()
            .reset_index(name="n_hours")
        )
    else:
        counts = (
            df.groupby(["_date_str"])["hour_business"]
            .nunique()
            .reset_index(name="n_hours")
        )

    # Check each expected row
    if is_prediction:
        for (day, model), grp in expected_grid.groupby(["business_day", "model_name"]):
            n_expected = len(grp["hour_business"].unique())  # 24
            match = counts[(counts["_date_str"] == day) & (counts["model_name"] == model)]
            if match.empty:
                errors.append({
                    "ledger": label,
                    "day": day,
                    "model": model,
                    "hour_business": "all",
                    "error": f"missing all {int(n_expected)} rows — model completely absent",
                    "detail": f"0/{int(n_expected)}",
                })
            else:
                n_actual = int(match.iloc[0]["n_hours"])
                if n_actual < n_expected:
                    errors.append({
                        "ledger": label,
                        "day": day,
                        "model": model,
                        "hour_business": f"only {n_actual}/{int(n_expected)} hours",
                        "error": f"incomplete coverage: {n_actual}/{int(n_expected)} hours",
                        "detail": f"{n_actual}/{int(n_expected)}",
                    })
    else:
        for day in expected_grid["business_day"].unique():
            n_expected = 24
            match = counts[counts["_date_str"] == day]
            if match.empty:
                errors.append({
                    "ledger": label,
                    "day": day,
                    "error": f"missing all {n_expected} rows — day completely absent",
                    "detail": f"0/{n_expected}",
                })
            else:
                n_actual = int(match.iloc[0]["n_hours"])
                if n_actual < n_expected:
                    errors.append({
                        "ledger": label,
                        "day": day,
                        "error": f"incomplete coverage: {n_actual}/{n_expected} hours",
                        "detail": f"{n_actual}/{n_expected}",
                    })


def _build_summary_counts(
    ledger_paths: dict[str, Path],
    target_date: str,
    days: int,
    da_pred_grid: pd.DataFrame,
    rt_pred_grid: pd.DataFrame,
) -> dict:
    """Build summary of expected vs actual row counts for each ledger."""
    summary: dict[str, Any] = {}

    for label_key, label in [
        ("dayahead_prediction_expected_rows", "dayahead prediction"),
    ]:
        summary[label_key] = len(da_pred_grid)

    summary["realtime_prediction_expected_rows"] = len(rt_pred_grid)
    summary["dayahead_actual_expected_rows"] = days * 24
    summary["realtime_actual_expected_rows"] = days * 24

    ledger_labels = {
        "dayahead prediction": "dayahead_prediction_actual_rows",
        "realtime prediction": "realtime_prediction_actual_rows",
        "dayahead actual": "dayahead_actual_actual_rows",
        "realtime actual": "realtime_actual_actual_rows",
    }

    for ll, key in ledger_labels.items():
        path = ledger_paths.get(ll)
        if path and path.exists():
            try:
                df = pd.read_parquet(path)
                summary[key] = len(df)
            except Exception:
                summary[key] = 0
        else:
            summary[key] = 0

    return summary


# ---------------------------------------------------------------------------
# Daily submission validation  (section 3 in design)
# ---------------------------------------------------------------------------


def validate_daily_submission(
    runs_root: str | Path,
    target_date: str,
    allow_degraded: bool = False,
) -> dict:
    """Validate a single day's submission_ready.csv and run_manifest.json.

    Parameters
    ----------
    runs_root : str | Path
        Root directory for run outputs (e.g. ``outputs/runs``).
    target_date : str
        Business day YYYY-MM-DD.
    allow_degraded : bool
        If True, DEGRADED_DELIVERED delivery_status is accepted as PASS.

    Returns
    -------
    dict with status, errors, warnings.
    """
    runs_root = Path(runs_root)
    errors: list[str] = []
    warnings: list[str] = []

    run_dir = runs_root / target_date
    sub_path = run_dir / "final" / "submission_ready.csv"
    manifest_path = run_dir / "run_manifest.json"

    # 1. File existence
    if not sub_path.exists():
        errors.append(f"submission_ready.csv not found: {sub_path}")
        return _submission_result("FAIL", errors, warnings, sub_path, manifest_path)

    try:
        df = pd.read_csv(sub_path)
    except Exception as exc:
        errors.append(f"cannot read {sub_path}: {exc}")
        return _submission_result("FAIL", errors, warnings, sub_path, manifest_path)

    # 2. Columns exact match
    actual_cols = list(df.columns)
    if actual_cols != SUBMISSION_COLUMNS:
        errors.append(
            f"column mismatch: expected {SUBMISSION_COLUMNS}, got {actual_cols}"
        )

    # 3. Row count
    if len(df) != 24:
        errors.append(f"row count: expected 24, got {len(df)}")

    # 4. hour_business 1..24
    if "hour_business" in df.columns:
        df["hour_business"] = pd.to_numeric(df["hour_business"], errors="coerce")
        hours = sorted(df["hour_business"].dropna().unique())
        if hours != list(range(1, 25)):
            errors.append(f"hour_business range: expected 1..24, got {hours}")
    else:
        errors.append("column hour_business missing")

    # 5. No duplicate hours
    if "hour_business" in df.columns:
        dups = df[df["hour_business"].duplicated()]["hour_business"].tolist()
        if dups:
            errors.append(f"duplicate hour_business: {dups}")

    # 6. business_day all match target_date
    if "business_day" in df.columns:
        bdays = df["business_day"].unique()
        if len(bdays) != 1 or str(bdays[0]) != target_date:
            errors.append(
                f"business_day mismatch: expected {target_date}, got {bdays}"
            )
    else:
        errors.append("column business_day missing")

    # 7. Hour-24 ds is target_date + 1 day 00:00:00
    if "hour_business" in df.columns and "ds" in df.columns:
        h24 = df[df["hour_business"] == 24]
        if not h24.empty:
            next_day = pd.Timestamp(target_date) + pd.Timedelta(days=1)
            expected_ds_prefix = next_day.strftime("%Y-%m-%d 00:00:00")
            actual_ds = str(h24.iloc[0]["ds"])
            if not actual_ds.startswith(expected_ds_prefix):
                errors.append(
                    f"hour-24 ds: expected '{expected_ds_prefix}', got '{actual_ds}'"
                )

    # 8. Price non-null and numeric
    for col in ("dayahead_price", "realtime_price"):
        if col in df.columns:
            null_mask = df[col].isna()
            if null_mask.any():
                bad_hours = df.loc[null_mask, "hour_business"].tolist()
                errors.append(f"{col}: null in hours {bad_hours}")
            try:
                pd.to_numeric(df[col], errors="raise")
            except (ValueError, TypeError) as exc:
                errors.append(f"{col}: non-numeric — {exc}")
        else:
            errors.append(f"column {col} missing")

    # 9. No _x/_y suffixes
    for col in df.columns:
        if col.endswith("_x") or col.endswith("_y"):
            errors.append(f"suffix column detected: '{col}'")

    # --- Manifest checks ---
    if not manifest_path.exists():
        errors.append(f"run_manifest.json not found: {manifest_path}")
        return _submission_result("FAIL", errors, warnings, sub_path, manifest_path)

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception as exc:
        errors.append(f"cannot read manifest: {exc}")
        return _submission_result("FAIL", errors, warnings, sub_path, manifest_path)

    # Check delivery_status if present
    delivery_status = manifest.get("delivery_status")
    if delivery_status == "FAILED_NO_DELIVERY":
        errors.append(f"delivery_status is FAILED_NO_DELIVERY — no usable output")
    elif delivery_status == "DEGRADED_DELIVERED" and not allow_degraded:
        errors.append(
            f"delivery_status is DEGRADED_DELIVERED — degraded output, "
            f"pass allow_degraded=True to accept"
        )

    # Five stages complete (skip if degraded and allowed)
    stages = manifest.get("stages", {})
    expected_stages = [
        "ledger_predict", "ledger_weight", "ledger_fuse",
        "ledger_classifier", "final_outputs",
    ]

    if delivery_status != "DEGRADED_DELIVERED" or not allow_degraded:
        for stage_name in expected_stages:
            stage = stages.get(stage_name, {})
            if stage.get("status") != "complete":
                errors.append(
                    f"stage '{stage_name}' status={stage.get('status', 'missing')}, "
                    f"expected 'complete'"
                )

    # Manifest errors
    manifest_errors = manifest.get("errors", [])
    if manifest_errors:
        errors.append(f"manifest has {len(manifest_errors)} error(s): {manifest_errors}")

    status = "PASS" if not errors else "FAIL"
    return _submission_result(status, errors, warnings, sub_path, manifest_path)


def _submission_result(
    status: str,
    errors: list,
    warnings: list,
    sub_path: Path,
    manifest_path: Path,
) -> dict:
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "submission_ready_path": str(sub_path),
        "manifest_path": str(manifest_path),
    }


# ---------------------------------------------------------------------------
# Next-day readiness  (section 3 in design)
# ---------------------------------------------------------------------------


def validate_next_day_readiness(
    target_date: str,
    ledger_root: str | Path,
    days: int = 30,
) -> dict:
    """Check whether tomorrow's D-30..D-1 ledger window is already complete.

    Called after today's run completes, to warn if the next day lacks
    sufficient ledger coverage.
    """
    next_date = pd.Timestamp(target_date) + pd.Timedelta(days=1)
    next_date_str = next_date.strftime("%Y-%m-%d")

    result = validate_ledger_window(next_date_str, ledger_root, days=days)
    return result
