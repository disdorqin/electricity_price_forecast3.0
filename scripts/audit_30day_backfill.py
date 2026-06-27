#!/usr/bin/env python
"""
30-Day Backfill Audit Script.

Comprehensive audit of the ledger_backfill results for the 30-day window.
Checks prediction ledger, actual ledger, daily run manifests, TimeMixer alignment,
business day conventions, and data quality.

Usage:
    python scripts/audit_30day_backfill.py
        --start 2026-01-25
        --end 2026-02-23
        --ledger-root outputs/ledger
        --runs-root outputs/runs
        --out outputs/audit_30day_2026-01-25_2026-02-23
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────

SEVERITY_P0 = "P0"
SEVERITY_P1 = "P1"
SEVERITY_P2 = "P2"

DAYAHEAD_MODELS = ["lightgbm", "timesfm", "timemixer"]
REALTIME_MODELS = ["timesfm", "sgdfnet", "timemixer", "rt916"]


@dataclass
class Problem:
    severity: str       # P0 / P1 / P2
    category: str       # e.g. "prediction_ledger", "actual_ledger", "daily_runs"
    task: str           # dayahead / realtime / general
    day: str            # target_day or "all"
    model: str          # model_name or "all"
    hour: int           # hour_business or -1 for all
    message: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "task": self.task,
            "day": self.day,
            "model": self.model,
            "hour": self.hour,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class AuditResult:
    problems: list[Problem] = field(default_factory=list)

    def add(self, sev: str, cat: str, task: str, day: str, model: str, hour: int, msg: str, detail: str = ""):
        self.problems.append(Problem(sev, cat, task, day, model, hour, msg, detail))

    @property
    def p0_count(self) -> int:
        return sum(1 for p in self.problems if p.severity == SEVERITY_P0)

    @property
    def p1_count(self) -> int:
        return sum(1 for p in self.problems if p.severity == SEVERITY_P1)

    @property
    def p2_count(self) -> int:
        return sum(1 for p in self.problems if p.severity == SEVERITY_P2)

    @property
    def is_pass(self) -> bool:
        return self.p0_count == 0


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def expected_days(start: str, end: str) -> list[str]:
    """Generate sorted list of expected target_days in YYYY-MM-DD format."""
    days: list[str] = []
    current = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    while current <= end_ts:
        days.append(current.strftime("%Y-%m-%d"))
        current += pd.Timedelta(days=1)
    return days


def expected_timestamp(business_day: str, hour_business: int) -> pd.Timestamp:
    """Return the canonical ds for a given business_day and hour_business.

    Rule:
      hour_business 1..23 → D 01:00 .. D 23:00
      hour_business 24    → D+1 00:00
    """
    d = pd.Timestamp(business_day).normalize()
    if hour_business == 24:
        return d + pd.Timedelta(days=1)
    return d + pd.Timedelta(hours=int(hour_business))


def read_parquet_safe(path: Path) -> pd.DataFrame | None:
    """Read parquet file, return None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning(f"Cannot read parquet {path}: {e}")
        return None


def read_csv_safe(path: Path) -> pd.DataFrame | None:
    """Read CSV file, return None if missing or corrupt."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        return pd.read_csv(path)
    except Exception as e:
        logger.warning(f"Cannot read CSV {path}: {e}")
        return None


def read_json_safe(path: Path) -> dict | None:
    """Read JSON file, return None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Cannot read JSON {path}: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────
# Core audit functions
# ──────────────────────────────────────────────────────────────────────

def audit_prediction_parquet(
    df: pd.DataFrame | None,
    task: str,
    expected_models: list[str],
    expected_days_list: list[str],
    start: str,
    end: str,
    result: AuditResult,
) -> dict:
    """Audit a prediction ledger parquet (dayahead or realtime).

    Returns a summary dict for reporting.
    """
    summary: dict = {
        "file_exists": df is not None,
        "rows_in_window": 0,
        "expected_rows": len(expected_days_list) * len(expected_models) * 24,
        "days_in_window": 0,
        "expected_days": len(expected_days_list),
        "extra_days": [],
        "models_found": [],
        "duplicate_keys": 0,
        "bad_day_model_24h": 0,
        "timestamp_mismatch": 0,
        "nan_y_pred": 0,
        "inf_y_pred": 0,
        "hour_0_count": 0,
        "hour_24_mismatch": 0,
        "y_pred_all_zero": False,
        "y_pred_extreme": 0,
        "status": "FAIL",
    }

    cat = "prediction_ledger"

    # P0: file must exist
    if df is None:
        result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                   f"Prediction ledger parquet missing for {task}")
        summary["status"] = "FAIL"
        return summary

    # Filter to window
    df["target_day_str"] = df["target_day"].astype(str)
    in_window = df["target_day_str"].isin(expected_days_list)
    df_window = df[in_window].copy()
    df_extra = df[~in_window].copy()

    summary["rows_in_window"] = len(df_window)
    summary["extra_days"] = sorted(df_extra["target_day_str"].unique().tolist()) if len(df_extra) > 0 else []

    # P0: row count
    expected = summary["expected_rows"]
    if len(df_window) != expected:
        result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                   f"{task} prediction: rows_in_window={len(df_window)}, expected={expected}")

    # Days coverage
    window_days = sorted(df_window["target_day_str"].unique().tolist())
    summary["days_in_window"] = len(window_days)
    missing_days = sorted(set(expected_days_list) - set(window_days))
    if missing_days:
        result.add(SEVERITY_P0, cat, task, ",".join(missing_days), "all", -1,
                   f"Missing target_days: {missing_days}")

    # Models found
    summary["models_found"] = sorted(df_window["model_name"].unique().tolist())
    missing_models = sorted(set(expected_models) - set(summary["models_found"]))
    if missing_models:
        result.add(SEVERITY_P0, cat, task, "all", ",".join(missing_models), -1,
                   f"Missing models: {missing_models}")

    # P0: duplicate keys
    key_cols = ["task", "model_name", "forecast_date", "target_day", "business_day", "hour_business"]
    existing_keys = [c for c in key_cols if c in df_window.columns]
    dup_count = df_window.duplicated(subset=existing_keys).sum()
    summary["duplicate_keys"] = int(dup_count)
    if dup_count > 0:
        dups_df = df_window[df_window.duplicated(subset=existing_keys, keep=False)].sort_values(existing_keys)
        detail = dups_df[existing_keys].head(20).to_string()
        result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                   f"{task} prediction: {dup_count} duplicate keys", detail=detail)

    # P0/P1: per day per model 24-hour check
    bad_day_model = 0
    for (day, model), grp in df_window.groupby(["target_day_str", "model_name"]):
        if len(grp) != 24:
            bad_day_model += 1
            hours_found = sorted(grp["hour_business"].dropna().astype(int).unique().tolist())
            result.add(SEVERITY_P0, cat, task, day, model, -1,
                       f"Expected 24 rows, got {len(grp)}. Hours: {hours_found}")
    summary["bad_day_model_24h"] = bad_day_model

    # P0: hour_business checks
    if "hour_business" in df_window.columns:
        hb = df_window["hour_business"]
        hour_0_count = int((hb == 0).sum())
        summary["hour_0_count"] = hour_0_count
        if hour_0_count > 0:
            bad_rows = df_window[hb == 0]
            result.add(SEVERITY_P0, cat, task, "all", "all", 0,
                       f"hour_business=0 found: {hour_0_count} rows",
                       detail=bad_rows[existing_keys].head(10).to_string())

    # P0: business_day == target_day
    if "business_day" in df_window.columns and "target_day_str" in df_window.columns:
        bd_mismatch = (df_window["business_day"].astype(str) != df_window["target_day_str"]).sum()
        if bd_mismatch > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                       f"{bd_mismatch} rows where business_day != target_day")

    # P0: timestamp alignment
    if all(c in df_window.columns for c in ["ds", "business_day", "hour_business"]):
        tm_mismatch = 0
        h24_mismatch = 0
        for idx, row in df_window.iterrows():
            try:
                ds = pd.Timestamp(row["ds"])
                expected_ts = expected_timestamp(str(row["business_day"]), int(row["hour_business"]))
                if ds != expected_ts:
                    tm_mismatch += 1
                    if tm_mismatch <= 5:  # log first 5
                        result.add(SEVERITY_P0, cat, task, str(row.get("target_day_str", "?")),
                                   str(row.get("model_name", "?")), int(row["hour_business"]),
                                   f"Timestamp mismatch: ds={ds}, expected={expected_ts}")
                # Check hour 24 special
                if int(row["hour_business"]) == 24 and ds.hour != 0:
                    h24_mismatch += 1
            except Exception:
                pass
        summary["timestamp_mismatch"] = tm_mismatch
        summary["hour_24_mismatch"] = h24_mismatch
        if tm_mismatch > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                       f"Timestamp mismatches: {tm_mismatch} rows (first 5 reported above)")
        if h24_mismatch > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", 24,
                       f"Hour 24 not mapping to D+1 00:00: {h24_mismatch} rows")

    # P0: y_pred NaN / inf
    if "y_pred" in df_window.columns:
        nan_count = int(df_window["y_pred"].isna().sum())
        summary["nan_y_pred"] = nan_count
        if nan_count > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                       f"y_pred NaN count: {nan_count}")

        inf_count = int((df_window["y_pred"] == float("inf")).sum() +
                        (df_window["y_pred"] == float("-inf")).sum())
        summary["inf_y_pred"] = inf_count
        if inf_count > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                       f"y_pred inf count: {inf_count}")

        # P1: extreme values
        extreme = int((df_window["y_pred"].abs() > 10000).sum())
        summary["y_pred_extreme"] = extreme
        if extreme > 0:
            result.add(SEVERITY_P1, cat, task, "all", "all", -1,
                       f"y_pred extreme values (|y|>10000): {extreme}")

        # P1: all zero
        all_zero = (df_window["y_pred"] == 0).all()
        summary["y_pred_all_zero"] = bool(all_zero)
        if all_zero:
            result.add(SEVERITY_P1, cat, task, "all", "all", -1,
                       "y_pred ALL ZERO across entire ledger")

    # Determine status
    p0_in_cat = any(p.severity == SEVERITY_P0 and p.category == cat and p.task == task
                    for p in result.problems)
    summary["status"] = "PASS" if not p0_in_cat else "FAIL"

    return summary


def audit_actual_parquet(
    df: pd.DataFrame | None,
    task: str,
    expected_days_list: list[str],
    result: AuditResult,
) -> dict:
    """Audit an actual ledger parquet."""
    summary: dict = {
        "file_exists": df is not None,
        "rows_in_window": 0,
        "expected_rows": len(expected_days_list) * 24,
        "days_in_window": 0,
        "expected_days": len(expected_days_list),
        "duplicate_keys": 0,
        "bad_day_24h": 0,
        "timestamp_mismatch": 0,
        "nan_y_true": 0,
        "inf_y_true": 0,
        "hour_0_count": 0,
        "hour_24_mismatch": 0,
        "status": "FAIL",
    }

    cat = "actual_ledger"

    if df is None:
        result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                   f"Actual ledger parquet missing for {task}")
        summary["status"] = "FAIL"
        return summary

    # Filter to window
    df["target_day_str"] = df["target_day"].astype(str)
    in_window = df["target_day_str"].isin(expected_days_list)
    df_window = df[in_window].copy()

    summary["rows_in_window"] = len(df_window)
    expected = summary["expected_rows"]
    if len(df_window) != expected:
        result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                   f"Rows in window: {len(df_window)}, expected {expected}")

    # Days coverage
    window_days = sorted(df_window["target_day_str"].unique().tolist())
    summary["days_in_window"] = len(window_days)
    missing_days = sorted(set(expected_days_list) - set(window_days))
    if missing_days:
        result.add(SEVERITY_P0, cat, task, ",".join(missing_days), "all", -1,
                   f"Missing target_days: {missing_days}")

    # Duplicates
    key_cols = ["task", "target_day", "business_day", "hour_business"]
    existing_keys = [c for c in key_cols if c in df_window.columns]
    dup_count = df_window.duplicated(subset=existing_keys).sum()
    summary["duplicate_keys"] = int(dup_count)
    if dup_count > 0:
        result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                   f"Duplicate keys: {dup_count}")

    # Per-day 24h check
    bad_day = 0
    for day, grp in df_window.groupby("target_day_str"):
        if len(grp) != 24:
            bad_day += 1
            result.add(SEVERITY_P0, cat, task, day, "all", -1,
                       f"Expected 24 rows, got {len(grp)}")
    summary["bad_day_24h"] = bad_day

    # hour_business validity
    if "hour_business" in df_window.columns:
        h0 = int((df_window["hour_business"] == 0).sum())
        summary["hour_0_count"] = h0
        if h0 > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", 0,
                       f"hour_business=0: {h0} rows")

    # Timestamp alignment
    if all(c in df_window.columns for c in ["ds", "business_day", "hour_business"]):
        tm = 0
        h24m = 0
        for idx, row in df_window.iterrows():
            try:
                ds = pd.Timestamp(row["ds"])
                exp = expected_timestamp(str(row["business_day"]), int(row["hour_business"]))
                if ds != exp:
                    tm += 1
                if int(row["hour_business"]) == 24 and ds.hour != 0:
                    h24m += 1
            except Exception:
                pass
        summary["timestamp_mismatch"] = tm
        summary["hour_24_mismatch"] = h24m
        if tm > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                       f"Timestamp mismatches: {tm}")
        if h24m > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", 24,
                       f"Hour 24 not D+1 00:00: {h24m}")

    # y_true NaN / inf
    if "y_true" in df_window.columns:
        nan = int(df_window["y_true"].isna().sum())
        summary["nan_y_true"] = nan
        if nan > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                       f"y_true NaN: {nan}")

        inf = int((df_window["y_true"] == float("inf")).sum() +
                  (df_window["y_true"] == float("-inf")).sum())
        summary["inf_y_true"] = inf
        if inf > 0:
            result.add(SEVERITY_P0, cat, task, "all", "all", -1,
                       f"y_true inf: {inf}")

    p0_in_cat = any(p.severity == SEVERITY_P0 and p.category == cat and p.task == task
                    for p in result.problems)
    summary["status"] = "PASS" if not p0_in_cat else "FAIL"

    return summary


def audit_daily_runs(
    runs_root: Path,
    expected_days_list: list[str],
    result: AuditResult,
) -> dict:
    """Audit daily run manifests and long tables."""
    summary: dict = {
        "manifests_found": 0,
        "failed_manifests": 0,
        "complete_with_warnings": 0,
        "dayahead_long_bad_rows": 0,
        "realtime_long_bad_rows": 0,
        "cutoff_mismatch": 0,
        "failed_models": [],
        "warning_details": [],
        "status": "PASS",
    }

    cat = "daily_runs"

    for day in expected_days_list:
        run_dir = runs_root / day
        manifest = read_json_safe(run_dir / "run_manifest.json")

        if manifest is None:
            result.add(SEVERITY_P0, cat, "general", day, "all", -1,
                       f"run_manifest.json missing for {day}")
            continue

        summary["manifests_found"] += 1

        # Check status
        status = manifest.get("status", "unknown")
        if status == "failed":
            summary["failed_manifests"] += 1
            errors = manifest.get("errors", [])
            result.add(SEVERITY_P0, cat, "general", day, "all", -1,
                       f"Manifest FAILED: {errors}")
        elif status == "complete_with_warnings":
            summary["complete_with_warnings"] += 1
            warnings = manifest.get("warnings", [])
            summary["warning_details"].append({"day": day, "warnings": warnings})
            result.add(SEVERITY_P1, cat, "general", day, "all", -1,
                       f"complete_with_warnings: {warnings}")

        # Check for failed models
        for task_name in ["dayahead", "realtime"]:
            task_results = manifest.get("results", {}).get(task_name, {})
            for model_name, model_info in task_results.items():
                if isinstance(model_info, dict) and model_info.get("status") == "failed":
                    summary["failed_models"].append(f"{day}/{task_name}/{model_name}")
                    result.add(SEVERITY_P0, cat, task_name, day, model_name, -1,
                               f"Model FAILED: {model_info.get('error', 'unknown')}")

        # Check cutoff hours
        cutoff_rt = manifest.get("realtime_cutoff_hour", None)
        if cutoff_rt is not None and cutoff_rt != 14:
            summary["cutoff_mismatch"] += 1
            result.add(SEVERITY_P0, cat, "realtime", day, "all", -1,
                       f"realtime_cutoff_hour={cutoff_rt}, expected 14")

        # Check data_cutoff_realtime
        dcr = manifest.get("data_cutoff_realtime", "")
        expected_dcr = f"{pd.Timestamp(day) - pd.Timedelta(days=1)}".split()[0]
        if dcr and not dcr.startswith(expected_dcr):
            summary["cutoff_mismatch"] += 1
            result.add(SEVERITY_P1, cat, "realtime", day, "all", -1,
                       f"data_cutoff_realtime={dcr}, expected prefix {expected_dcr}")

        # Check model_runtime_config
        runtime = manifest.get("model_runtime_config", {})
        tm_cfg = runtime.get("timemixer", {})
        if tm_cfg.get("cutoff_hour_rt") not in (None, 14):
            summary["cutoff_mismatch"] += 1
            result.add(SEVERITY_P0, cat, "realtime", day, "timemixer", -1,
                       f"timemixer cutoff_hour_rt={tm_cfg.get('cutoff_hour_rt')}, expected 14")
        rt_cfg = runtime.get("rt916", {})
        if rt_cfg.get("asof_hour") not in (None, 14):
            summary["cutoff_mismatch"] += 1
            result.add(SEVERITY_P0, cat, "realtime", day, "rt916", -1,
                       f"rt916 asof_hour={rt_cfg.get('asof_hour')}, expected 14")

        # Check long table row counts
        da_long_path = run_dir / "dayahead" / "prediction" / "all_model_predictions_long.csv"
        da_long = read_csv_safe(da_long_path)
        if da_long is not None and len(da_long) != 72:
            summary["dayahead_long_bad_rows"] += 1
            result.add(SEVERITY_P1, cat, "dayahead", day, "all", -1,
                       f"dayahead long table rows: {len(da_long)}, expected 72")

        rt_long_path = run_dir / "realtime" / "prediction" / "all_model_predictions_long.csv"
        rt_long = read_csv_safe(rt_long_path)
        if rt_long is not None and len(rt_long) != 96:
            summary["realtime_long_bad_rows"] += 1
            result.add(SEVERITY_P1, cat, "realtime", day, "all", -1,
                       f"realtime long table rows: {len(rt_long)}, expected 96")

    # Summary status
    if summary["failed_manifests"] > 0 or summary["failed_models"]:
        summary["status"] = "FAIL"
    elif summary["complete_with_warnings"] > 0:
        summary["status"] = "PASS_WITH_WARNINGS"
    else:
        summary["status"] = "PASS"

    return summary


def audit_timemixer_specific(
    df_da: pd.DataFrame | None,
    df_rt: pd.DataFrame | None,
    expected_days_list: list[str],
    result: AuditResult,
) -> dict:
    """Specific TimeMixer alignment checks."""
    summary: dict = {
        "dayahead_bad_rows": 0,
        "realtime_bad_rows": 0,
        "hour_0_count": 0,
        "d_00_00_count": 0,
        "hour_24_mismatch_count": 0,
        "status": "PASS",
    }

    cat = "timemixer_alignment"

    for task_label, df, models_list in [
        ("dayahead", df_da, ["timemixer"]),
        ("realtime", df_rt, ["timemixer"]),
    ]:
        if df is None:
            result.add(SEVERITY_P0, cat, task_label, "all", "timemixer", -1,
                       "No prediction ledger for TimeMixer check")
            summary["status"] = "FAIL"
            continue

        tm = df[df["model_name"].str.lower() == "timemixer"].copy()
        if tm.empty:
            result.add(SEVERITY_P0, cat, task_label, "all", "timemixer", -1,
                       "No TimeMixer rows in prediction ledger")
            summary["status"] = "FAIL"
            continue

        # Per-day per-model 24h
        for (day, model), grp in tm.groupby(["target_day", "model_name"]):
            if len(grp) != 24:
                summary["dayahead_bad_rows" if task_label == "dayahead" else "realtime_bad_rows"] += 1
                result.add(SEVERITY_P0, cat, task_label, str(day), str(model), -1,
                           f"Not 24 rows: {len(grp)}")

        # hour_business = 0
        if "hour_business" in tm.columns:
            h0 = int((tm["hour_business"] == 0).sum())
            summary["hour_0_count"] += h0
            if h0 > 0:
                result.add(SEVERITY_P0, cat, task_label, "all", "timemixer", 0,
                           f"hour_business=0: {h0} rows")

        # D 00:00 detection: any ds with hour=0 and hour_business != 24
        if all(c in tm.columns for c in ["ds", "hour_business"]):
            ts = pd.to_datetime(tm["ds"])
            midnight = ts[ts.apply(lambda x: x.hour == 0)]
            if not midnight.empty:
                wrong_midnight = midnight[tm.loc[midnight.index, "hour_business"].astype(int) != 24]
                summary["d_00_00_count"] += len(wrong_midnight)
                if len(wrong_midnight) > 0:
                    result.add(SEVERITY_P0, cat, task_label, "all", "timemixer", -1,
                               f"D 00:00 with hour_business != 24: {len(wrong_midnight)} rows",
                               detail=str(wrong_midnight.head(10)))

        # hour 24 timestamp
        if all(c in tm.columns for c in ["ds", "business_day", "hour_business"]):
            h24 = tm[tm["hour_business"].astype(int) == 24]
            for idx, row in h24.iterrows():
                ds = pd.Timestamp(row["ds"])
                bd = pd.Timestamp(row["business_day"]).normalize()
                expected_ds = bd + pd.Timedelta(days=1)
                if ds != expected_ds:
                    summary["hour_24_mismatch_count"] += 1
                    result.add(SEVERITY_P0, cat, task_label,
                               str(row.get("target_day", "?")), "timemixer", 24,
                               f"Hour 24 ds={ds}, expected={expected_ds}")

    p0_in_cat = any(p.severity == SEVERITY_P0 and p.category == cat
                    for p in result.problems)
    summary["status"] = "PASS" if not p0_in_cat else "FAIL"

    return summary


def check_csv_duplicates(parquet_df: pd.DataFrame | None, task: str, kind: str, ledger_root: Path, result: AuditResult):
    """Check that CSV copy exists and matches parquet."""
    cat = "file_integrity"
    csv_path = ledger_root / task / kind / f"{kind}_ledger.csv"
    if not csv_path.exists():
        result.add(SEVERITY_P1, cat, task, "all", "all", -1,
                   f"CSV copy missing: {csv_path}")
        return

    if parquet_df is not None:
        csv_df = read_csv_safe(csv_path)
        if csv_df is not None and len(csv_df) != len(parquet_df):
            result.add(SEVERITY_P1, cat, task, "all", "all", -1,
                       f"CSV rows={len(csv_df)} vs parquet rows={len(parquet_df)}")


# ──────────────────────────────────────────────────────────────────────
# Report writing
# ──────────────────────────────────────────────────────────────────────

def write_reports(
    out_dir: Path,
    start: str,
    end: str,
    expected_days_list: list[str],
    pred_summaries: dict,
    actual_summaries: dict,
    daily_summary: dict,
    tm_summary: dict,
    result: AuditResult,
):
    """Write all output files."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build full report dict
    report = {
        "audit_meta": {
            "script": "scripts/audit_30day_backfill.py",
            "audit_time": datetime.now(timezone.utc).isoformat(),
            "window_start": start,
            "window_end": end,
            "expected_days": len(expected_days_list),
        },
        "prediction_ledger": pred_summaries,
        "actual_ledger": actual_summaries,
        "daily_runs": daily_summary,
        "timemixer_alignment": tm_summary,
        "problems": [p.to_dict() for p in result.problems],
        "summary": {
            "p0_count": result.p0_count,
            "p1_count": result.p1_count,
            "p2_count": result.p2_count,
            "final_status": "PASS" if result.is_pass else "FAIL",
        },
    }

    # Write JSON
    json_path = out_dir / "audit_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Report JSON: {json_path}")

    # Write problems.csv
    problems_csv_path = out_dir / "problems.csv"
    if result.problems:
        pdf = pd.DataFrame([p.to_dict() for p in result.problems])
        pdf.to_csv(problems_csv_path, index=False)
    else:
        pd.DataFrame(columns=["severity", "category", "task", "day", "model", "hour", "message", "detail"]
                     ).to_csv(problems_csv_path, index=False)
    logger.info(f"Problems CSV: {problems_csv_path}")

    # Write prediction summary
    if pred_summaries:
        rows = []
        for task, s in pred_summaries.items():
            s_copy = dict(s)
            s_copy["task"] = task
            rows.append(s_copy)
        pd.DataFrame(rows).to_csv(out_dir / "prediction_summary.csv", index=False)

    # Write actual summary
    if actual_summaries:
        rows = []
        for task, s in actual_summaries.items():
            s_copy = dict(s)
            s_copy["task"] = task
            rows.append(s_copy)
        pd.DataFrame(rows).to_csv(out_dir / "actual_summary.csv", index=False)

    # Write manifest summary
    if daily_summary:
        pd.DataFrame([daily_summary]).to_csv(out_dir / "manifest_summary.csv", index=False)

    # Write markdown report
    md = _generate_markdown_report(
        start, end, expected_days_list,
        pred_summaries, actual_summaries, daily_summary, tm_summary, result,
    )
    md_path = out_dir / "audit_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"Report MD: {md_path}")

    # Print console summary
    print(_generate_console_summary(
        start, end, expected_days_list,
        pred_summaries, actual_summaries, daily_summary, tm_summary, result,
    ))


def _generate_console_summary(
    start: str, end: str, expected_days_list: list[str],
    pred_summaries: dict, actual_summaries: dict,
    daily_summary: dict, tm_summary: dict,
    result: AuditResult,
) -> str:
    """Generate console-friendly summary."""
    lines = [
        "=== 30-Day Backfill Audit ===",
        f"Window: {start} ~ {end}",
        f"Expected days: {len(expected_days_list)}",
        "",
    ]

    # Prediction ledger
    lines.append("[Prediction Ledger]")
    for task in ["dayahead", "realtime"]:
        s = pred_summaries.get(task, {})
        lines.append(f"{task}:")
        lines.append(f"  rows_in_window: {s.get('rows_in_window', '?')} / expected {s.get('expected_rows', '?')}")
        lines.append(f"  days: {s.get('days_in_window', '?')} / {s.get('expected_days', '?')}")
        lines.append(f"  models: {', '.join(s.get('models_found', []))}")
        lines.append(f"  duplicate_keys: {s.get('duplicate_keys', '?')}")
        lines.append(f"  bad_day_model_24h: {s.get('bad_day_model_24h', '?')}")
        lines.append(f"  timestamp_mismatch: {s.get('timestamp_mismatch', '?')}")
        lines.append(f"  nan_y_pred: {s.get('nan_y_pred', '?')}")
        lines.append(f"  inf_y_pred: {s.get('inf_y_pred', '?')}")
        lines.append(f"  status: {s.get('status', 'FAIL')}")
        lines.append("")

    # Actual ledger
    lines.append("[Actual Ledger]")
    for task in ["dayahead", "realtime"]:
        s = actual_summaries.get(task, {})
        lines.append(f"{task}:")
        lines.append(f"  rows_in_window: {s.get('rows_in_window', '?')} / expected {s.get('expected_rows', '?')}")
        lines.append(f"  days: {s.get('days_in_window', '?')} / {s.get('expected_days', '?')}")
        lines.append(f"  duplicate_keys: {s.get('duplicate_keys', '?')}")
        lines.append(f"  bad_day_24h: {s.get('bad_day_24h', '?')}")
        lines.append(f"  timestamp_mismatch: {s.get('timestamp_mismatch', '?')}")
        lines.append(f"  nan_y_true: {s.get('nan_y_true', '?')}")
        lines.append(f"  status: {s.get('status', 'FAIL')}")
        lines.append("")

    # Daily runs
    lines.append("[Daily Runs]")
    lines.append(f"  manifests_found: {daily_summary.get('manifests_found', '?')} / {len(expected_days_list)}")
    lines.append(f"  failed_manifests: {daily_summary.get('failed_manifests', '?')}")
    lines.append(f"  complete_with_warnings: {daily_summary.get('complete_with_warnings', '?')}")
    lines.append(f"  dayahead_long_bad_rows: {daily_summary.get('dayahead_long_bad_rows', '?')}")
    lines.append(f"  realtime_long_bad_rows: {daily_summary.get('realtime_long_bad_rows', '?')}")
    lines.append(f"  cutoff_mismatch: {daily_summary.get('cutoff_mismatch', '?')}")
    lines.append(f"  status: {daily_summary.get('status', 'FAIL')}")
    lines.append("")

    # TimeMixer
    lines.append("[TimeMixer Alignment]")
    lines.append(f"  dayahead_bad_rows: {tm_summary.get('dayahead_bad_rows', '?')}")
    lines.append(f"  realtime_bad_rows: {tm_summary.get('realtime_bad_rows', '?')}")
    lines.append(f"  hour_0_count: {tm_summary.get('hour_0_count', '?')}")
    lines.append(f"  D_00:00_wrong: {tm_summary.get('d_00_00_count', '?')}")
    lines.append(f"  hour_24_mismatch: {tm_summary.get('hour_24_mismatch_count', '?')}")
    lines.append(f"  status: {tm_summary.get('status', 'FAIL')}")
    lines.append("")

    # Problems
    lines.append("[Problems]")
    lines.append(f"  P0: {result.p0_count}")
    lines.append(f"  P1: {result.p1_count}")
    lines.append(f"  P2: {result.p2_count}")
    lines.append("")

    lines.append(f"FINAL_STATUS: {'PASS' if result.is_pass else 'FAIL'}")
    return "\n".join(lines)


def _generate_markdown_report(
    start: str, end: str, expected_days_list: list[str],
    pred_summaries: dict, actual_summaries: dict,
    daily_summary: dict, tm_summary: dict,
    result: AuditResult,
) -> str:
    """Generate markdown report."""
    lines = [
        "# 30-Day Backfill Audit Report",
        "",
        f"- **Window**: {start} ~ {end}",
        f"- **Expected Days**: {len(expected_days_list)}",
        f"- **Audit Time**: {datetime.now(timezone.utc).isoformat()}",
        f"- **FINAL STATUS**: **{'PASS' if result.is_pass else 'FAIL'}**",
        "",
    ]

    # Prediction ledger
    lines.append("## Prediction Ledger")
    for task in ["dayahead", "realtime"]:
        s = pred_summaries.get(task, {})
        lines.append(f"### {task}")
        lines.append(f"| Check | Result |")
        lines.append(f"|-------|--------|")
        lines.append(f"| Rows in window | {s.get('rows_in_window', '?')} / {s.get('expected_rows', '?')} |")
        lines.append(f"| Days | {s.get('days_in_window', '?')} / {s.get('expected_days', '?')} |")
        lines.append(f"| Models | {', '.join(s.get('models_found', []))} |")
        lines.append(f"| Extra days | {s.get('extra_days', [])} |")
        lines.append(f"| Duplicate keys | {s.get('duplicate_keys', '?')} |")
        lines.append(f"| Bad day/model 24h | {s.get('bad_day_model_24h', '?')} |")
        lines.append(f"| Timestamp mismatch | {s.get('timestamp_mismatch', '?')} |")
        lines.append(f"| Hour 0 count | {s.get('hour_0_count', '?')} |")
        lines.append(f"| Hour 24 mismatch | {s.get('hour_24_mismatch', '?')} |")
        lines.append(f"| NaN y_pred | {s.get('nan_y_pred', '?')} |")
        lines.append(f"| Inf y_pred | {s.get('inf_y_pred', '?')} |")
        lines.append(f"| Extreme y_pred | {s.get('y_pred_extreme', '?')} |")
        lines.append(f"| y_pred all zero | {s.get('y_pred_all_zero', False)} |")
        lines.append(f"| **Status** | **{s.get('status', 'FAIL')}** |")
        lines.append("")

    # Actual ledger
    lines.append("## Actual Ledger")
    for task in ["dayahead", "realtime"]:
        s = actual_summaries.get(task, {})
        lines.append(f"### {task}")
        lines.append(f"| Check | Result |")
        lines.append(f"|-------|--------|")
        lines.append(f"| Rows in window | {s.get('rows_in_window', '?')} / {s.get('expected_rows', '?')} |")
        lines.append(f"| Days | {s.get('days_in_window', '?')} / {s.get('expected_days', '?')} |")
        lines.append(f"| Duplicate keys | {s.get('duplicate_keys', '?')} |")
        lines.append(f"| Bad day 24h | {s.get('bad_day_24h', '?')} |")
        lines.append(f"| Timestamp mismatch | {s.get('timestamp_mismatch', '?')} |")
        lines.append(f"| Hour 0 count | {s.get('hour_0_count', '?')} |")
        lines.append(f"| NaN y_true | {s.get('nan_y_true', '?')} |")
        lines.append(f"| Inf y_true | {s.get('inf_y_true', '?')} |")
        lines.append(f"| **Status** | **{s.get('status', 'FAIL')}** |")
        lines.append("")

    # Daily runs
    lines.append("## Daily Runs")
    lines.append(f"| Check | Result |")
    lines.append(f"|-------|--------|")
    lines.append(f"| Manifests found | {daily_summary.get('manifests_found', '?')} / {len(expected_days_list)} |")
    lines.append(f"| Failed manifests | {daily_summary.get('failed_manifests', '?')} |")
    lines.append(f"| Complete with warnings | {daily_summary.get('complete_with_warnings', '?')} |")
    lines.append(f"| Dayahead long bad rows | {daily_summary.get('dayahead_long_bad_rows', '?')} |")
    lines.append(f"| Realtime long bad rows | {daily_summary.get('realtime_long_bad_rows', '?')} |")
    lines.append(f"| Cutoff mismatch | {daily_summary.get('cutoff_mismatch', '?')} |")
    lines.append(f"| Failed models | {daily_summary.get('failed_models', [])} |")
    lines.append(f"| **Status** | **{daily_summary.get('status', 'FAIL')}** |")
    if daily_summary.get("warning_details"):
        lines.append("")
        lines.append("### Complete-with-Warnings Details")
        for w in daily_summary["warning_details"]:
            lines.append(f"- **{w['day']}**: {w['warnings']}")
    lines.append("")

    # TimeMixer alignment
    lines.append("## TimeMixer Alignment")
    lines.append(f"| Check | Result |")
    lines.append(f"|-------|--------|")
    lines.append(f"| Dayahead bad rows | {tm_summary.get('dayahead_bad_rows', '?')} |")
    lines.append(f"| Realtime bad rows | {tm_summary.get('realtime_bad_rows', '?')} |")
    lines.append(f"| Hour 0 count | {tm_summary.get('hour_0_count', '?')} |")
    lines.append(f"| D 00:00 wrong | {tm_summary.get('d_00_00_count', '?')} |")
    lines.append(f"| Hour 24 mismatch | {tm_summary.get('hour_24_mismatch_count', '?')} |")
    lines.append(f"| **Status** | **{tm_summary.get('status', 'FAIL')}** |")
    lines.append("")

    # Problems
    lines.append("## Problems")
    lines.append(f"| Severity | Count |")
    lines.append(f"|----------|-------|")
    lines.append(f"| P0 | {result.p0_count} |")
    lines.append(f"| P1 | {result.p1_count} |")
    lines.append(f"| P2 | {result.p2_count} |")
    lines.append("")
    if result.problems:
        lines.append("### Detailed Problem List")
        for p in result.problems:
            lines.append(f"- **[{p.severity}]** {p.category}/{p.task}: {p.message}")
            if p.detail:
                lines.append(f"  - Detail: {p.detail[:200]}")
    lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Audit 30-day backfill results")
    parser.add_argument("--start", default="2026-01-25", help="Window start YYYY-MM-DD")
    parser.add_argument("--end", default="2026-02-23", help="Window end YYYY-MM-DD")
    parser.add_argument("--ledger-root", default="outputs/ledger", help="Ledger root directory")
    parser.add_argument("--runs-root", default="outputs/runs", help="Daily runs root directory")
    parser.add_argument("--out", default=None, help="Output directory for reports")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    start = args.start
    end = args.end
    ledger_root = Path(args.ledger_root)
    runs_root = Path(args.runs_root)
    out_dir = Path(args.out) if args.out else Path(f"outputs/audit_{start}_{end}")

    expected_days_list = expected_days(start, end)
    result = AuditResult()

    # ── 1. Prediction ledger ──
    logger.info("Loading prediction ledgers...")
    da_pred = read_parquet_safe(ledger_root / "dayahead" / "prediction" / "prediction_ledger.parquet")
    rt_pred = read_parquet_safe(ledger_root / "realtime" / "prediction" / "prediction_ledger.parquet")

    pred_summaries = {}
    pred_summaries["dayahead"] = audit_prediction_parquet(
        da_pred, "dayahead", DAYAHEAD_MODELS, expected_days_list, start, end, result)
    pred_summaries["realtime"] = audit_prediction_parquet(
        rt_pred, "realtime", REALTIME_MODELS, expected_days_list, start, end, result)

    # CSV copy check
    check_csv_duplicates(da_pred, "dayahead", "prediction", ledger_root, result)
    check_csv_duplicates(rt_pred, "realtime", "prediction", ledger_root, result)

    # ── 2. Actual ledger ──
    logger.info("Loading actual ledgers...")
    da_act = read_parquet_safe(ledger_root / "dayahead" / "actual" / "actual_ledger.parquet")
    rt_act = read_parquet_safe(ledger_root / "realtime" / "actual" / "actual_ledger.parquet")

    actual_summaries = {}
    actual_summaries["dayahead"] = audit_actual_parquet(da_act, "dayahead", expected_days_list, result)
    actual_summaries["realtime"] = audit_actual_parquet(rt_act, "realtime", expected_days_list, result)

    # CSV copy check
    check_csv_duplicates(da_act, "dayahead", "actual", ledger_root, result)
    check_csv_duplicates(rt_act, "realtime", "actual", ledger_root, result)

    # ── 3. Daily runs ──
    logger.info("Auditing daily run manifests...")
    daily_summary = audit_daily_runs(runs_root, expected_days_list, result)

    # ── 4. TimeMixer specific ──
    logger.info("Checking TimeMixer alignment...")
    tm_summary = audit_timemixer_specific(da_pred, rt_pred, expected_days_list, result)

    # ── 5. Write reports ──
    logger.info("Writing reports...")
    write_reports(out_dir, start, end, expected_days_list,
                  pred_summaries, actual_summaries, daily_summary, tm_summary, result)

    # Exit code
    return 0 if result.is_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
