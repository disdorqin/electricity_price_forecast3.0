#!/usr/bin/env python
"""
Range pipeline delivery acceptance verification.

Checks every day in [start, end] for complete five-stage output,
valid ``submission_ready.csv``, delivery_status consistency, and
range-level artifact completeness.

Exit codes:
  0 — all checks pass (or degraded passed with --allow-degraded)
  2 — degraded delivery (--allow-degraded not given)
  1 — hard failure
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

# Ensure we can import from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipelines.delivery_quality import validate_daily_submission


EXPECTED_STAGES = [
    "ledger_predict", "ledger_weight", "ledger_fuse",
    "ledger_classifier", "final_outputs",
]


def verify_range(
    start_date: str,
    end_date: str,
    runs_root: str = "outputs/runs",
    allow_degraded: bool = False,
) -> int:
    """Verify range pipeline output for [start, end].

    Returns 0 on pass, 2 on degraded-but-passed, 1 on failure.
    """
    runs_root_p = Path(runs_root)
    errors: list[str] = []
    warnings: list[str] = []
    degraded_detected = False

    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    expected_dates = [d.strftime("%Y-%m-%d") for d in date_range]

    print(f"VERIFY_RANGE_PIPELINE: {start_date} to {end_date}")
    print(f"  expected days: {len(expected_dates)}")
    print()

    # ------------------------------------------------------------------
    # 1. range_manifest.json exists
    # ------------------------------------------------------------------
    range_dir_name = f"range_{start_date}_to_{end_date}"
    range_dir = runs_root_p / range_dir_name
    range_manifest_path = range_dir / "range_manifest.json"

    if not range_manifest_path.exists():
        errors.append("RANGE_MANIFEST: range_manifest.json not found")
        print(f"\n  errors: {len(errors)}")
        for e in errors:
            print(f"    - {e}")
        print("  status: FAIL")
        return 1

    with open(range_manifest_path) as f:
        range_manifest = json.load(f)

    # 2. Manifest status
    manifest_status = range_manifest.get("status", "unknown")
    delivery_status = range_manifest.get("delivery_status", "UNKNOWN")

    print(f"  range status: {manifest_status}")
    print(f"  delivery status: {delivery_status}")

    if manifest_status == "preflight_failed":
        errors.append(
            f"RANGE_MANIFEST: status is 'preflight_failed' — range did not execute. "
            f"Fix preflight errors and retry."
        )
    elif manifest_status == "interrupted":
        errors.append("RANGE_MANIFEST: status is 'interrupted' — range was cancelled")
    elif manifest_status in ("failed", "partial"):
        errors.append(f"RANGE_MANIFEST: status is '{manifest_status}' — some days failed")
    elif manifest_status not in ("complete", "complete_with_degraded_days", "all_skipped"):
        errors.append(f"RANGE_MANIFEST: unexpected status '{manifest_status}'")

    if delivery_status == "FAILED_NO_DELIVERY":
        errors.append("RANGE_MANIFEST: delivery_status is FAILED_NO_DELIVERY")
    elif delivery_status == "DEGRADED_DELIVERED":
        degraded_detected = True
        if not allow_degraded:
            errors.append(
                "RANGE_MANIFEST: delivery_status is DEGRADED_DELIVERED — "
                "pass --allow-degraded to accept"
            )

    # 3. total_days matches
    manifest_total = range_manifest.get("total_days", 0)
    if manifest_total != len(expected_dates):
        errors.append(
            f"RANGE_MANIFEST: total_days={manifest_total}, "
            f"expected {len(expected_dates)}"
        )

    # 4. daily_results dates match expected set
    result_dates = set()
    for dr in range_manifest.get("daily_results", []):
        result_dates.add(dr.get("date", ""))
    expected_set = set(expected_dates)
    if result_dates != expected_set:
        missing = expected_set - result_dates
        extra = result_dates - expected_set
        if missing:
            errors.append(
                f"RANGE_MANIFEST.daily_results: missing dates: {sorted(missing)}"
            )
        if extra:
            errors.append(
                f"RANGE_MANIFEST.daily_results: extra dates: {sorted(extra)}"
            )

    # 5. range_summary.csv exists and has correct dates
    range_summary_path = range_dir / "range_summary.csv"
    if not range_summary_path.exists():
        errors.append(f"RANGE_SUMMARY: range_summary.csv not found at {range_summary_path}")
    else:
        try:
            summary_df = pd.read_csv(range_summary_path)
            summary_dates = set(summary_df["date"].unique())
            if summary_dates != expected_set:
                s_missing = expected_set - summary_dates
                s_extra = summary_dates - expected_set
                if s_missing:
                    errors.append(
                        f"RANGE_SUMMARY: missing dates: {sorted(s_missing)}"
                    )
                if s_extra:
                    errors.append(
                        f"RANGE_SUMMARY: extra dates: {sorted(s_extra)}"
                    )
            print(f"  range_summary.csv: {len(summary_df)} rows")
        except Exception as exc:
            errors.append(f"RANGE_SUMMARY: cannot read {range_summary_path}: {exc}")

    # 6. completed + failed + skipped count matches total
    completed = range_manifest.get("completed_days", 0)
    failed = range_manifest.get("failed_days", 0)
    skipped = range_manifest.get("skipped_days", 0)
    degraded = range_manifest.get("degraded_days", 0)
    if completed + failed + skipped != manifest_total:
        errors.append(
            f"RANGE_MANIFEST: completed({completed}) + failed({failed}) + "
            f"skipped({skipped}) = {completed + failed + skipped}, "
            f"expected total_days={manifest_total}"
        )
    if degraded > completed:
        errors.append(
            f"RANGE_MANIFEST: degraded_days({degraded}) > "
            f"completed_days({completed}) — impossible"
        )

    # ------------------------------------------------------------------
    # 7. Per-day checks using validate_daily_submission
    # ------------------------------------------------------------------
    for dr in range_manifest.get("daily_results", []):
        d = dr.get("date", "")
        day_ds = dr.get("delivery_status", dr.get("status", "?"))

        print(f"\n--- {d} (delivery={day_ds}) ---")

        # If DEGRADED_DELIVERED, check fallback report exists
        if day_ds == "DEGRADED_DELIVERED":
            fb_md = runs_root_p / d / "final" / "fallback_report.md"
            fb_json = runs_root_p / d / "final" / "fallback_report.json"
            # Also check range delivery report
            range_fb_md = range_dir / "range_delivery_report.md"
            if not fb_md.exists() and not fb_json.exists() and not range_fb_md.exists():
                errors.append(
                    f"{d}: DEGRADED_DELIVERED but no fallback_report.md/json "
                    f"or range_delivery_report.md found"
                )
            else:
                print(f"  fallback report present")

        # Validate submission
        is_valid = True
        # For DEGRADED_DELIVERED days, always validate but allow_degraded
        local_allow = allow_degraded or day_ds == "DEGRADED_DELIVERED"
        sub_result = validate_daily_submission(
            runs_root_p, d, allow_degraded=local_allow,
        )

        if sub_result["status"] == "PASS":
            print(f"  PASS")
        else:
            is_valid = False
            for e in sub_result["errors"][:5]:
                errors.append(f"{d}: {e}")
                print(f"    - {e}")
            if len(sub_result["errors"]) > 5:
                more = len(sub_result["errors"]) - 5
                errors.append(f"{d}: ... and {more} more errors")
                print(f"    ... and {more} more errors")
            print(f"  FAIL")

    # ------------------------------------------------------------------
    # 8. Final verdict
    # ------------------------------------------------------------------
    print(f"\n  errors: {len(errors)}, warnings: {len(warnings)}")
    if errors:
        print(f"  status: FAIL")
        for e in errors[:10]:
            print(f"    - {e}")
        if degraded_detected and not allow_degraded:
            return 2
        return 1
    else:
        if degraded_detected and not allow_degraded:
            print(f"\n  FINAL_STATUS: DEGRADED (pass --allow-degraded to accept)")
            return 2
        print(f"\n  FINAL_STATUS: PASS")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify range pipeline output (delivery acceptance)"
    )
    parser.add_argument("--start", required=True, help="Range start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Range end date YYYY-MM-DD")
    parser.add_argument(
        "--runs-root", default="outputs/runs",
        help="Root directory for run outputs",
    )
    parser.add_argument(
        "--allow-degraded", action="store_true", default=False,
        help="Accept DEGRADED_DELIVERED as pass (exit 0 instead of 2)",
    )
    args = parser.parse_args()
    return verify_range(args.start, args.end, args.runs_root, args.allow_degraded)


if __name__ == "__main__":
    raise SystemExit(main())
