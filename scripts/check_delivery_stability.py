#!/usr/bin/env python
"""
Synthetic delivery stability tests — no GPU, no model inference, no real data.

Tests:
  1. validate_daily_submission PASS
  2. validate_daily_submission FAIL (missing hour + bad columns)
  3. validate_ledger_window PASS (30 days synthetic parquet)
  4. validate_ledger_window catches missing model-day
  5. validate_ledger_window catches missing hour
  6. emergency_fallback creates degraded delivery
  7. Delivery status exit code mapping
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

# Ensure we can import from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipelines.delivery_quality import (
    validate_daily_submission,
    validate_ledger_window,
)
from pipelines.emergency_fallback import try_emergency_fallback


PASS = 0
FAIL = 1
results: list[tuple[str, int, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    msg = f"PASS: {name}" if status == PASS else f"FAIL: {name}"
    if detail and status == FAIL:
        msg += f" — {detail}"
    results.append((name, status, detail))
    print(msg)


def test_daily_submission_valid() -> str:
    """Test 1: validate_daily_submission PASS with synthetic data."""
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td)
        target_date = "2026-02-24"

        # Create run directory structure
        run_dir = runs_root / target_date / "final"
        run_dir.mkdir(parents=True)

        # Create submission_ready.csv
        rows = []
        for h in range(1, 25):
            if h <= 23:
                ds = f"2026-02-24 {h:02d}:00:00"
            else:
                ds = "2026-02-25 00:00:00"
            if 1 <= h <= 8:
                period = "1_8"
            elif 9 <= h <= 16:
                period = "9_16"
            else:
                period = "17_24"
            rows.append({
                "business_day": target_date,
                "ds": ds,
                "hour_business": h,
                "period": period,
                "dayahead_price": 100.0 + h,
                "realtime_price": 90.0 + h,
            })
        sub_df = pd.DataFrame(rows)
        sub_df.to_csv(run_dir / "submission_ready.csv", index=False)

        # Create run_manifest.json with five complete stages
        manifest = {
            "pipeline": "ledger_full",
            "target_date": target_date,
            "status": "complete",
            "delivery_status": "NORMAL",
            "stages": {
                "ledger_predict": {"status": "complete"},
                "ledger_weight": {"status": "complete"},
                "ledger_fuse": {"status": "complete"},
                "ledger_classifier": {"status": "complete"},
                "final_outputs": {"status": "complete"},
            },
            "errors": [],
            "warnings": [],
        }
        with open(runs_root / target_date / "run_manifest.json", "w") as f:
            json.dump(manifest, f)

        result = validate_daily_submission(runs_root, target_date)
        check(
            "daily submission valid",
            result["status"] == "PASS",
            f"expected PASS, got {result['status']}: {result['errors']}",
        )
    return f"test_daily_submission_valid: {'PASS' if results[-1][1] == PASS else 'FAIL'}"


def test_daily_submission_fail() -> str:
    """Test 2: validate_daily_submission FAIL with bad data."""
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td)
        target_date = "2026-02-24"

        run_dir = runs_root / target_date / "final"
        run_dir.mkdir(parents=True)

        # Create bad submission — missing hour 13, wrong column names
        rows = []
        for h in range(1, 24):  # intentionally skip hour 24, miss hour 13
            if h == 13:
                continue
            if h <= 23:
                ds = f"2026-02-24 {h:02d}:00:00"
            else:
                ds = "2026-02-25 00:00:00"
            if 1 <= h <= 8:
                period = "1_8"
            elif 9 <= h <= 16:
                period = "9_16"
            else:
                period = "17_24"
            rows.append({
                "business_day": target_date,
                "ds": ds,
                "hour_business": h,
                "period": period,
                "dayahead_price_x": 100.0 + h,  # bad suffix
                "realtime_price_y": 90.0 + h,   # bad suffix
            })
        sub_df = pd.DataFrame(rows)
        sub_df.to_csv(run_dir / "submission_ready.csv", index=False)

        manifest = {
            "pipeline": "ledger_full",
            "target_date": target_date,
            "delivery_status": "NORMAL",
            "stages": {
                "ledger_predict": {"status": "complete"},
                "ledger_weight": {"status": "complete"},
                "ledger_fuse": {"status": "complete"},
                "ledger_classifier": {"status": "complete"},
                "final_outputs": {"status": "complete"},
            },
            "errors": [],
        }
        with open(runs_root / target_date / "run_manifest.json", "w") as f:
            json.dump(manifest, f)

        result = validate_daily_submission(runs_root, target_date)
        has_hour_error = any("hour" in e.lower() or "24" in e for e in result["errors"])
        has_column_error = any("column" in e.lower() or "suffix" in e.lower() or "_x" in e or "_y" in e for e in result["errors"])

        check(
            "daily submission catches missing hour",
            result["status"] == "FAIL" and has_hour_error,
            f"expected FAIL with hour error, got {result['status']}: {result['errors'][:3]}",
        )
        check(
            "daily submission catches bad columns",
            result["status"] == "FAIL" and has_column_error,
            f"expected FAIL with column error, got {result['status']}: {result['errors'][:3]}",
        )
    return ""


def _make_synthetic_ledger(
    ledger_root: Path,
    target_date: str,
    days: int = 30,
    task: str = "dayahead",
    is_prediction: bool = True,
    drop_model_day: str | None = None,
    drop_model_name: str | None = None,
    drop_hour_day: str | None = None,
    drop_hour_model: str | None = None,
    drop_hour: int | None = None,
):
    """Helper to create synthetic parquet ledger files for testing."""
    start_dt = pd.Timestamp(target_date)
    window_end = start_dt - pd.Timedelta(days=1)
    window_start = start_dt - pd.Timedelta(days=days)

    date_range = pd.date_range(start=window_start, end=window_end, freq="D")

    if is_prediction:
        if task == "dayahead":
            models = ["lightgbm", "timesfm", "timemixer"]
        else:
            models = ["timesfm", "sgdfnet", "timemixer", "rt916"]
        rows = []
        for d in date_range:
            d_str = d.strftime("%Y-%m-%d")
            for model in models:
                for h in range(1, 25):
                    # Drop entire model-day
                    if drop_model_day and drop_model_name:
                        if d_str == drop_model_day and model == drop_model_name:
                            continue
                    # Drop single hour
                    if drop_hour_day and drop_hour_model and drop_hour is not None:
                        if d_str == drop_hour_day and model == drop_hour_model and h == drop_hour:
                            continue
                    rows.append({
                        "target_day": d_str,
                        "business_day": d_str,
                        "hour_business": h,
                        "model_name": model,
                        "y_pred": 100.0 + h + (0.1 * models.index(model)),
                    })
        df = pd.DataFrame(rows)
    else:
        rows = []
        for d in date_range:
            d_str = d.strftime("%Y-%m-%d")
            for h in range(1, 25):
                rows.append({
                    "target_day": d_str,
                    "business_day": d_str,
                    "hour_business": h,
                    "y_true": 100.0 + h,
                })
        df = pd.DataFrame(rows)

    if is_prediction:
        sub_dir = ledger_root / task / "prediction"
    else:
        sub_dir = ledger_root / task / "actual"
    sub_dir.mkdir(parents=True, exist_ok=True)

    if is_prediction:
        df.to_parquet(sub_dir / "prediction_ledger.parquet", index=False)
    else:
        df.to_parquet(sub_dir / "actual_ledger.parquet", index=False)


def test_ledger_window_pass() -> str:
    """Test 3: validate_ledger_window PASS with complete synthetic data."""
    with tempfile.TemporaryDirectory() as td:
        ledger_root = Path(td)
        target_date = "2026-02-24"

        # Create all four ledgers with full data
        for task in ["dayahead", "realtime"]:
            _make_synthetic_ledger(ledger_root, target_date, task=task, is_prediction=True)
            _make_synthetic_ledger(ledger_root, target_date, task=task, is_prediction=False)

        result = validate_ledger_window(target_date, ledger_root)
        check(
            "ledger window valid",
            result["status"] == "PASS",
            f"expected PASS, got {result['status']}: {len(result['errors'])} errors",
        )
    return ""


def test_ledger_window_missing_model_day() -> str:
    """Test 4: validate_ledger_window catches a completely missing model-day."""
    with tempfile.TemporaryDirectory() as td:
        ledger_root = Path(td)
        target_date = "2026-02-24"

        # Create three ledgers with full data, realtime prediction missing rt916 on one day
        for task in ["dayahead", "realtime"]:
            _make_synthetic_ledger(ledger_root, target_date, task=task, is_prediction=True)
            _make_synthetic_ledger(ledger_root, target_date, task=task, is_prediction=False)

        # Rebuild realtime prediction with rt916 dropped on one day
        _make_synthetic_ledger(
            ledger_root, target_date, task="realtime", is_prediction=True,
            drop_model_day="2026-02-01", drop_model_name="rt916",
        )

        result = validate_ledger_window(target_date, ledger_root)
        has_rt916_error = any(
            e.get("model") == "rt916" and "0/24" in str(e.get("detail", ""))
            for e in result["errors"]
        )

        check(
            "ledger window catches missing model-day",
            result["status"] == "FAIL" and has_rt916_error,
            f"expected FAIL with rt916/0/24, got {result['status']}: "
            f"{[e for e in result['errors'][:5]]}",
        )
    return ""


def test_ledger_window_missing_hour() -> str:
    """Test 5: validate_ledger_window catches a single missing hour."""
    with tempfile.TemporaryDirectory() as td:
        ledger_root = Path(td)
        target_date = "2026-02-24"

        # Create full data then rebuild dayahead prediction with lightgbm missing hour 13
        for task in ["dayahead", "realtime"]:
            _make_synthetic_ledger(ledger_root, target_date, task=task, is_prediction=True)
            _make_synthetic_ledger(ledger_root, target_date, task=task, is_prediction=False)

        # Rebuild day ahead prediction with lightgbm's hour 13 removed on one day
        _make_synthetic_ledger(
            ledger_root, target_date, task="dayahead", is_prediction=True,
            drop_hour_day="2026-02-01", drop_hour_model="lightgbm", drop_hour=13,
        )

        result = validate_ledger_window(target_date, ledger_root)
        has_hour13_error = any(
            e.get("model") == "lightgbm" and ("23/24" in str(e.get("detail", "")) or "13" in str(e.get("hour_business", "")))
            for e in result["errors"]
        )

        check(
            "ledger window catches missing hour",
            result["status"] == "FAIL" and has_hour13_error,
            f"expected FAIL with hour 13/23/24 error, got {result['status']}: "
            f"{[e for e in result['errors'][:3]]}",
        )
    return ""


def test_emergency_fallback() -> str:
    """Test 6: emergency fallback creates degraded delivery."""
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td)
        data_path = Path(td) / "history.csv"
        target_date = "2026-02-24"

        # Create synthetic historical data
        rows = []
        for day_offset in range(1, 41):  # 40 days of history
            d = pd.Timestamp(target_date) - pd.Timedelta(days=day_offset)
            for h in range(24):
                ts = d + pd.Timedelta(hours=h)
                rows.append({
                    "ds": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "日前电价": 100.0 + h + (day_offset * 0.1),
                    "实时电价": 90.0 + h + (day_offset * 0.1),
                })
        hist_df = pd.DataFrame(rows)
        hist_df.to_csv(data_path, index=False)

        # Run fallback
        result = try_emergency_fallback(
            target_date, data_path, runs_root,
            reason="test: synthetic failure",
        )

        check(
            "emergency fallback returns success",
            result["success"],
            f"expected success, got: {result.get('errors', [])}",
        )

        # Check that submission_ready.csv was created
        sub_path = runs_root / target_date / "final" / "submission_ready.csv"
        check(
            "emergency fallback creates submission_ready.csv",
            sub_path.exists(),
            f"file not found: {sub_path}",
        )

        if sub_path.exists():
            sub_df = pd.read_csv(sub_path)
            check(
                "fallback submission has 24 rows",
                len(sub_df) == 24,
                f"expected 24, got {len(sub_df)}",
            )
            expected_cols = [
                "business_day", "ds", "hour_business", "period",
                "dayahead_price", "realtime_price",
            ]
            check(
                "fallback submission has correct columns",
                list(sub_df.columns) == expected_cols,
                f"got {list(sub_df.columns)}",
            )

            # Now validate via validate_daily_submission
            # Need to create a run_manifest.json with DEGRADED_DELIVERED
            fb_json_path = runs_root / target_date / "final" / "fallback_report.json"
            fb_exists = fb_json_path.exists()
            check(
                "fallback creates fallback_report.json",
                fb_exists,
                f"not found: {fb_json_path}",
            )

        # Also test with the manifest check:
        # The fallback itself doesn't create a run_manifest, so validation with
        # allow_degraded=False will fail (no manifest). The integration in
        # ledger_full creates the manifest separately.
        # For this test, we check that the fallback generated useable files.
        check(
            "fallback creates fallback_report.md",
            (runs_root / target_date / "final" / "fallback_report.md").exists(),
            "fallback_report.md not found",
        )

    return ""


def test_exit_code_mapping() -> str:
    """Test 7: delivery status exit code mapping."""
    from pipelines.delivery_report import _delivery_exit_code

    checks = [
        (_delivery_exit_code("NORMAL"), 0, "NORMAL -> 0"),
        (_delivery_exit_code("DEGRADED_DELIVERED"), 2, "DEGRADED_DELIVERED -> 2"),
        (_delivery_exit_code("FAILED_NO_DELIVERY"), 1, "FAILED_NO_DELIVERY -> 1"),
        (_delivery_exit_code("UNKNOWN"), 1, "UNKNOWN -> 1 (default)"),
    ]

    for actual, expected, label in checks:
        check(
            f"exit code: {label}",
            actual == expected,
            f"expected {expected}, got {actual}",
        )
    return ""


def main() -> int:
    print("=" * 60)
    print("CHECK_DELIVERY_STABILITY")
    print("=" * 60)

    test_daily_submission_valid()
    test_daily_submission_fail()
    test_ledger_window_pass()
    test_ledger_window_missing_model_day()
    test_ledger_window_missing_hour()
    test_emergency_fallback()
    test_exit_code_mapping()

    print()

    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)

    for name, status, detail in results:
        marker = "PASS" if status == PASS else "FAIL"
        print(f"{marker}: {name}")
        if status == FAIL and detail:
            print(f"  {detail}")

    print()
    print(f"RESULT: {passed}/{len(results)} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
