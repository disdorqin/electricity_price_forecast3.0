#!/usr/bin/env python
"""Verify range pipeline output for ledger_full_range."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def verify_range(
    start_date: str,
    end_date: str,
    runs_root: str = "outputs/runs",
) -> int:
    """Verify range pipeline output for [start, end].

    Returns 0 on pass, 1 on failure.
    """
    runs_root_p = Path(runs_root)
    errors = []

    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    expected_dates = [d.strftime("%Y-%m-%d") for d in date_range]

    print(f"VERIFY_RANGE_PIPELINE: {start_date} to {end_date}")
    print(f"  expected days: {len(expected_dates)}")
    print()

    # Check each day's submission_ready.csv
    for d in expected_dates:
        sub_path = runs_root_p / d / "final" / "submission_ready.csv"
        if not sub_path.exists():
            errors.append(f"MISSING {d}: submission_ready.csv not found at {sub_path}")
            continue

        try:
            df = pd.read_csv(sub_path)
        except Exception as e:
            errors.append(f"ERROR {d}: cannot read {sub_path}: {e}")
            continue

        # Check row count
        if len(df) != 24:
            errors.append(f"ROW_COUNT {d}: expected 24 rows, got {len(df)}")

        # Check required columns
        expected_cols = {"business_day", "ds", "hour_business", "period", "dayahead_price", "realtime_price"}
        actual_cols = set(df.columns)
        missing_cols = expected_cols - actual_cols
        if missing_cols:
            errors.append(f"COLUMNS {d}: missing {missing_cols}")

        # Check no _x/_y suffix columns
        for col in df.columns:
            if col.endswith("_x") or col.endswith("_y"):
                errors.append(f"SUFFIX {d}: column '{col}' has _x/_y suffix")

        # Check hour_business 1..24
        if "hour_business" in df.columns:
            hours = sorted(df["hour_business"].unique())
            if hours != list(range(1, 25)):
                errors.append(f"HOURS {d}: expected 1..24, got {hours}")

        # Check hour 24 ds = D+1 00:00
        if "hour_business" in df.columns and "ds" in df.columns:
            h24_rows = df[df["hour_business"] == 24]
            if not h24_rows.empty:
                next_day = pd.Timestamp(d) + pd.Timedelta(days=1)
                expected_ds = next_day.strftime("%Y-%m-%d %H:%M:%S")
                actual_ds = str(h24_rows.iloc[0]["ds"])
                if expected_ds not in actual_ds:
                    errors.append(f"HOUR24_DS {d}: expected '{expected_ds}', got '{actual_ds}'")

        print(f"  {d}: PASS ({len(df)} rows, cols={list(df.columns)})")

    # Check range summary if it exists
    range_dir_name = f"range_{start_date}_to_{end_date}"
    range_dir = runs_root_p / range_dir_name
    range_summary_path = range_dir / "range_summary.csv"
    range_manifest_path = range_dir / "range_manifest.json"

    if range_summary_path.exists():
        try:
            summary_df = pd.read_csv(range_summary_path)
            summary_dates = summary_df["date"].unique()
            if len(summary_dates) != len(expected_dates):
                errors.append(
                    f"RANGE_SUMMARY: expected {len(expected_dates)} dates, "
                    f"got {len(summary_dates)}"
                )
            print(f"\n  range_summary.csv: {len(summary_df)} rows")
        except Exception as e:
            errors.append(f"RANGE_SUMMARY: cannot read: {e}")

    if range_manifest_path.exists():
        print(f"  range_manifest.json: present")
    else:
        errors.append("RANGE_MANIFEST: range_manifest.json not found")

    # Final verdict
    print(f"\n  errors: {len(errors)}")
    if errors:
        print(f"  status: FAIL")
        for e in errors:
            print(f"    - {e}")
        return 1
    else:
        print(f"  status: PASS")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify range pipeline output")
    parser.add_argument("--start", required=True, help="Range start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Range end date YYYY-MM-DD")
    parser.add_argument("--runs-root", default="outputs/runs", help="Root directory for run outputs")
    args = parser.parse_args()
    return verify_range(args.start, args.end, args.runs_root)


if __name__ == "__main__":
    raise SystemExit(main())
