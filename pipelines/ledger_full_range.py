"""
Ledger full-range pipeline: daily runs for a date range.

Runs the complete ledger_full pipeline (five stages) for each day in [start, end]
and produces a range-level manifest and summary.

Range pipeline stages per day:
  ledger_predict → ledger_weight → ledger_fuse → ledger_classifier → final_outputs

Output:
  outputs/runs/range_{start}_to_{end}/
    range_manifest.json
    range_summary.csv
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SUBMISSION_COLUMNS = [
    "business_day", "ds", "hour_business", "period",
    "dayahead_price", "realtime_price",
]


def is_existing_final_valid(
    runs_root: Path, target_date: str
) -> tuple[bool, list[str]]:
    """Strong validation of an existing day's submission output.

    Returns (is_valid, reasons) where *reasons* lists each failed check.
    All of the following must pass for ``is_valid`` to be True:

    1. ``submission_ready.csv`` exists.
    2. Columns exactly equal ``SUBMISSION_COLUMNS``.
    3. Exactly 24 rows.
    4. ``hour_business`` is precisely 1 .. 24.
    5. No duplicate ``hour_business`` values.
    6. ``business_day`` equals *target_date*.
    7. Hour-24 ``ds`` is ``{target_date+1day} 00:00:00``.
    8. ``dayahead_price`` and ``realtime_price`` are non-null and numeric.
    9. No ``_x`` / ``_y`` suffix columns.
    10. ``run_manifest.json`` exists.
    11. All five stages in the manifest report ``status == "complete"``.
    12. Manifest ``errors`` list is empty.
    """
    reasons: list[str] = []
    run_dir = runs_root / target_date
    sub_path = run_dir / "final" / "submission_ready.csv"

    # 1. File existence
    if not sub_path.exists():
        return False, [f"submission_ready.csv not found: {sub_path}"]

    try:
        df = pd.read_csv(sub_path)
    except Exception as exc:
        return False, [f"cannot read {sub_path}: {exc}"]

    # 2. Column exact match
    actual_cols = list(df.columns)
    if actual_cols != SUBMISSION_COLUMNS:
        reasons.append(
            f"columns mismatch: expected {SUBMISSION_COLUMNS}, got {actual_cols}"
        )

    # 3. Row count
    if len(df) != 24:
        reasons.append(f"row count: expected 24, got {len(df)}")

    # 4. hour_business 1..24
    if "hour_business" in df.columns:
        hours = sorted(df["hour_business"].unique())
        if hours != list(range(1, 25)):
            reasons.append(f"hour_business range: expected 1..24, got {hours}")
    else:
        reasons.append("column hour_business missing")

    # 5. Duplicate hours
    if "hour_business" in df.columns and df["hour_business"].duplicated().any():
        dups = df[df["hour_business"].duplicated()]["hour_business"].tolist()
        reasons.append(f"duplicate hour_business values: {dups}")

    # 6. business_day match
    if "business_day" in df.columns:
        bdays = df["business_day"].unique()
        if len(bdays) != 1 or str(bdays[0]) != target_date:
            reasons.append(
                f"business_day mismatch: expected {target_date}, got {bdays}"
            )
    else:
        reasons.append("column business_day missing")

    # 7. Hour-24 ds
    if "hour_business" in df.columns and "ds" in df.columns:
        h24 = df[df["hour_business"] == 24]
        if not h24.empty:
            next_day = pd.Timestamp(target_date) + pd.Timedelta(days=1)
            expected_ds = next_day.strftime("%Y-%m-%d %H:%M:%S")
            actual_ds = str(h24.iloc[0]["ds"])
            if expected_ds not in actual_ds:
                reasons.append(
                    f"hour-24 ds: expected '{expected_ds}', got '{actual_ds}'"
                )

    # 8. Non-null numeric prices
    for col in ("dayahead_price", "realtime_price"):
        if col in df.columns:
            null_mask = df[col].isna()
            if null_mask.any():
                bad_hours = df.loc[null_mask, "hour_business"].tolist()
                reasons.append(f"{col}: null in hours {bad_hours}")
            try:
                pd.to_numeric(df[col], errors="raise")
            except (ValueError, TypeError) as exc:
                reasons.append(f"{col}: non-numeric values — {exc}")
        else:
            reasons.append(f"column {col} missing")

    # 9. No _x/_y suffixes
    for col in df.columns:
        if col.endswith("_x") or col.endswith("_y"):
            reasons.append(f"suffix column detected: '{col}'")

    # If structural checks failed, don't bother with manifest checks
    if reasons:
        return False, reasons

    # 10. Manifest exists
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return False, [f"run_manifest.json not found: {manifest_path}"]

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception as exc:
        return False, [f"cannot read manifest {manifest_path}: {exc}"]

    # 11. Five stages complete
    stages = manifest.get("stages", {})
    expected_stages = [
        "ledger_predict", "ledger_weight", "ledger_fuse",
        "ledger_classifier", "final_outputs",
    ]
    for stage_name in expected_stages:
        stage = stages.get(stage_name, {})
        if stage.get("status") != "complete":
            reasons.append(
                f"stage '{stage_name}' status={stage.get('status', 'missing')}, "
                f"expected 'complete'"
            )

    # 12. No manifest errors
    manifest_errors = manifest.get("errors", [])
    if manifest_errors:
        reasons.append(f"manifest has {len(manifest_errors)} error(s): {manifest_errors}")

    return len(reasons) == 0, reasons


def run_ledger_full_range(args: Any) -> dict:
    """
    Main entry for ``--pipeline ledger_full_range``.

    Orchestrates daily ``ledger_full`` (five stages) across *start* .. *end*
    inclusive.  Writes ``range_manifest.json`` and ``range_summary.csv``
    into ``outputs/runs/range_{start}_to_{end}/``.

    Parameters
    ----------
    args : argparse.Namespace
        Must contain: start, end, data_path, plus optional
        continue_on_error, skip_existing_final, range_preflight.

    Returns
    -------
    dict — the range-level manifest.
    """
    start_date = args.start
    end_date = args.end
    if not start_date or not end_date:
        raise ValueError("--start and --end are required for ledger_full_range")

    if start_date > end_date:
        raise ValueError(f"--start ({start_date}) > --end ({end_date})")

    continue_on_error = getattr(args, "continue_on_error", False)
    skip_existing_final = getattr(args, "skip_existing_final", False)
    range_preflight = getattr(args, "range_preflight", True)
    runs_root = Path(getattr(args, "runs_root", "outputs/runs"))

    logger.info(f"=== ledger_full_range: {start_date} to {end_date} ===")

    # Build date list (inclusive)
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    date_list = [d.strftime("%Y-%m-%d") for d in date_range]

    range_dir = runs_root / f"range_{start_date}_to_{end_date}"
    range_dir.mkdir(parents=True, exist_ok=True)

    # Initialise range manifest
    range_manifest: dict[str, Any] = {
        "pipeline": "ledger_full_range",
        "start_date": start_date,
        "end_date": end_date,
        "total_days": len(date_list),
        "completed_days": 0,
        "failed_days": 0,
        "skipped_days": 0,
        "degraded_days": 0,
        "status": "running",
        "delivery_status": "NORMAL",
        "daily_results": [],
        "errors": [],
        "warnings": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # ------------------------------------------------------------------
    # Preflight — delegate to delivery_quality.validate_ledger_window
    # ------------------------------------------------------------------
    if range_preflight:
        from pipelines.delivery_quality import validate_ledger_window

        ledger_root = Path(getattr(args, "ledger_root", "outputs/ledger"))
        preflight_result = validate_ledger_window(start_date, ledger_root)

        if preflight_result["status"] == "FAIL":
            range_manifest["preflight_report"] = preflight_result
            range_manifest["status"] = "preflight_failed"
            range_manifest["delivery_status"] = "FAILED_NO_DELIVERY"
            range_manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            range_manifest["note"] = (
                "Preflight validation failed. Run with --no-range-preflight to skip, "
                "or fix the reported issues and retry."
            )
            _write_range_artifacts(range_dir, range_manifest)
            console_msg = (
                f"[ledger_full_range] preflight FAILED — "
                f"{len(preflight_result['errors'])} error(s)\n"
                f"  Manifest: {range_dir / 'range_manifest.json'}\n"
                f"  Next steps: fix errors above or use --no-range-preflight\n"
            )
            for err in preflight_result["errors"][:10]:
                detail = err.get("error", str(err))
                ledger = err.get("ledger", "")
                day = err.get("day", "")
                model = err.get("model", "")
                if ledger:
                    console_msg += f"    [{ledger}] "
                if day:
                    console_msg += f"{day} "
                if model:
                    console_msg += f"model={model} "
                console_msg += f"— {detail}\n"
            logger.error(console_msg.strip())
            return range_manifest

        if preflight_result.get("warnings"):
            for w in preflight_result["warnings"]:
                logger.warning(f"Preflight warning: {w}")
                range_manifest["warnings"].append(w)

    # ------------------------------------------------------------------
    # Daily loop
    # ------------------------------------------------------------------
    from pipelines.ledger_full import run_ledger_full

    for target_date in date_list:
        logger.info(f"\n{'='*60}\nRange day: {target_date}\n{'='*60}")

        # --- skip-existing-final check ---
        if skip_existing_final:
            is_valid, skip_reasons = is_existing_final_valid(runs_root, target_date)
            if is_valid:
                logger.info(f"Skipping {target_date}: submission_ready.csv is valid")
                daily_manifest_path = runs_root / target_date / "run_manifest.json"
                range_manifest["daily_results"].append({
                    "date": target_date,
                    "status": "skipped",
                    "manifest_path": str(daily_manifest_path),
                    "submission_ready_path": str(runs_root / target_date / "final" / "submission_ready.csv"),
                    "warnings_count": 0,
                    "errors_count": 0,
                    "stage_statuses": {"ledger_full": "skipped"},
                    "skip_reason": "existing output valid",
                })
                range_manifest["skipped_days"] += 1
                _write_range_artifacts(range_dir, range_manifest)
                continue
            else:
                for r in skip_reasons:
                    logger.warning(f"  {target_date}: skip check failed — {r}")
                    range_manifest["warnings"].append(f"skip check failed for {target_date}: {r}")

        # --- run ledger_full for this day ---
        day_start_ts = time.time()
        day_args = _copy_args_for_day(args, target_date)
        day_result: dict[str, Any] = {}

        try:
            day_result = run_ledger_full(day_args)
            day_status = day_result.get("status", "unknown")
        except KeyboardInterrupt:
            range_manifest["status"] = "interrupted"
            range_manifest["errors"].append(f"Interrupted by user at {target_date}")
            _write_range_artifacts(range_dir, range_manifest)
            logger.error(f"Range interrupted at {target_date}")
            return range_manifest
        except Exception as exc:
            logger.exception(f"Range day {target_date} failed: {exc}")
            day_result = {"status": "error", "error": str(exc)}
            day_status = "error"

        day_elapsed = time.time() - day_start_ts

        # Record result
        daily_manifest_path = runs_root / target_date / "run_manifest.json"
        submission_path = runs_root / target_date / "final" / "submission_ready.csv"

        # Read delivery_status from the day result (ledger_full now sets it)
        day_delivery_status = day_result.get("delivery_status", "UNKNOWN")
        day_postflight = day_result.get("postflight", {})
        day_fallback = day_result.get("fallback", {})
        day_fallback_used = (
            day_fallback.get("fallback_used", False)
            if isinstance(day_fallback, dict)
            else False
        )

        day_entry: dict[str, Any] = {
            "date": target_date,
            "status": day_status,
            "delivery_status": day_delivery_status,
            "postflight_status": day_postflight.get("status", "NOT RUN"),
            "fallback_used": day_fallback_used,
            "started_at": day_result.get("started_at"),
            "completed_at": day_result.get("completed_at"),
            "duration_seconds": round(day_elapsed, 1),
            "manifest_path": str(daily_manifest_path),
            "submission_ready_path": str(submission_path) if submission_path.exists() else None,
            "stage_statuses": {},
            "errors_count": 0,
            "warnings_count": 0,
        }

        stages = day_result.get("stages", {})
        for stage_name, stage_data in stages.items():
            if isinstance(stage_data, dict):
                day_entry["stage_statuses"][stage_name] = stage_data.get("status", "unknown")
                day_entry["errors_count"] += 1 if stage_data.get("status") == "failed" else 0

        # Top-level errors/warnings from the day manifest
        day_entry["errors_count"] += len(day_result.get("errors", []))
        day_entry["warnings_count"] = len(day_result.get("warnings", []))

        range_manifest["daily_results"].append(day_entry)

        # Track using delivery_status
        if day_delivery_status == "NORMAL":
            range_manifest["completed_days"] += 1
        elif day_delivery_status == "DEGRADED_DELIVERED":
            range_manifest["completed_days"] += 1
            range_manifest["degraded_days"] += 1
            # Range-level delivery_status must be at least DEGRADED_DELIVERED
            if range_manifest["delivery_status"] == "NORMAL":
                range_manifest["delivery_status"] = "DEGRADED_DELIVERED"
        elif day_delivery_status == "FAILED_NO_DELIVERY":
            range_manifest["failed_days"] += 1
            range_manifest["delivery_status"] = "FAILED_NO_DELIVERY"
            err_msg = f"Day {target_date} delivery=FAILED_NO_DELIVERY"
            if day_result.get("error"):
                err_msg += f": {day_result['error']}"
            range_manifest["errors"].append(err_msg)
            if not continue_on_error:
                range_manifest["status"] = "failed"
                _write_range_artifacts(range_dir, range_manifest)
                logger.error(
                    f"Range stopped at {target_date} (FAILED_NO_DELIVERY, "
                    f"use --continue-on-error to continue)"
                )
                return range_manifest
        elif day_status in ("failed", "error"):
            # Status-based fallback (ledger_full may not have set delivery_status)
            range_manifest["failed_days"] += 1
            range_manifest["delivery_status"] = "FAILED_NO_DELIVERY"
            err_msg = f"Day {target_date} status={day_status}"
            if day_result.get("error"):
                err_msg += f": {day_result['error']}"
            range_manifest["errors"].append(err_msg)
            if not continue_on_error:
                range_manifest["status"] = "failed"
                _write_range_artifacts(range_dir, range_manifest)
                logger.error(f"Range stopped at {target_date} (use --continue-on-error to continue)")
                return range_manifest

        # Flush range state after each day
        _write_range_artifacts(range_dir, range_manifest)

    # ------------------------------------------------------------------
    # Final status + range delivery report
    # ------------------------------------------------------------------
    _finalise_range_manifest(range_manifest)
    _write_range_artifacts(range_dir, range_manifest)

    from pipelines.delivery_report import (
        write_range_delivery_report,
        print_range_delivery_report,
    )
    write_range_delivery_report(range_dir, range_manifest)
    print_range_delivery_report(range_manifest)

    logger.info(
        f"ledger_full_range {start_date}..{end_date}: "
        f"status={range_manifest['status']}, "
        f"delivery={range_manifest['delivery_status']}, "
        f"({range_manifest['completed_days']}/{range_manifest['total_days']} days, "
        f"{range_manifest['degraded_days']} degraded, "
        f"{range_manifest['failed_days']} failed, "
        f"{range_manifest['skipped_days']} skipped)"
    )
    return range_manifest


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _copy_args_for_day(args: Any, target_date: str) -> Any:
    """Create a namespace copy with the target date set for ledger_full."""
    day_args = copy.copy(args)
    day_args.date = target_date
    day_args.pipeline = "ledger_full"
    day_args.start = None
    day_args.end = None
    return day_args


def _finalise_range_manifest(manifest: dict) -> None:
    """Derive the final status and delivery_status of the range manifest."""
    total = manifest["total_days"]
    completed = manifest["completed_days"]
    failed = manifest["failed_days"]
    skipped = manifest["skipped_days"]
    degraded = manifest.get("degraded_days", 0)

    # Determine range status
    if failed == 0 and completed + skipped == total:
        if degraded > 0:
            manifest["status"] = "complete_with_degraded_days"
        else:
            manifest["status"] = "complete"
    elif failed > 0 and completed > 0:
        manifest["status"] = "partial"
    elif completed == 0 and skipped == total and total > 0:
        manifest["status"] = "all_skipped"
    elif failed == total:
        manifest["status"] = "failed"
    elif manifest["status"] not in ("preflight_failed", "interrupted"):
        manifest["status"] = "failed"

    # Determine delivery_status if it wasn't already set
    # (preflight already sets FAILED_NO_DELIVERY; daily loop sets others)
    ds = manifest.get("delivery_status", "NORMAL")
    if ds == "NORMAL" and degraded > 0:
        manifest["delivery_status"] = "DEGRADED_DELIVERED"
    elif ds == "DEGRADED_DELIVERED" and degraded == 0 and failed == 0:
        # All days passed normally — reset to NORMAL
        manifest["delivery_status"] = "NORMAL"

    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()


def _write_range_artifacts(range_dir: Path, manifest: dict) -> None:
    """Write both range_manifest.json and range_summary.csv."""
    manifest_path = range_dir / "range_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

    _write_range_summary(range_dir, manifest)


def _write_range_summary(range_dir: Path, manifest: dict) -> None:
    """Write range_summary.csv from manifest daily_results."""
    rows = []
    for dr in manifest.get("daily_results", []):
        sp = dr.get("submission_ready_path")
        submission_ready_exists = sp is not None and Path(sp).exists()
        submission_ready_rows = 0
        if submission_ready_exists:
            try:
                sr_df = pd.read_csv(sp)
                submission_ready_rows = len(sr_df)
            except Exception:
                pass

        rows.append({
            "date": dr["date"],
            "status": dr["status"],
            "submission_ready_exists": submission_ready_exists,
            "submission_ready_rows": submission_ready_rows,
            "errors_count": dr.get("errors_count", 0),
            "warnings_count": dr.get("warnings_count", 0),
            "manifest_path": dr.get("manifest_path", ""),
            "submission_ready_path": sp or "",
        })

    summary_df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["date", "status", "submission_ready_exists", "submission_ready_rows",
                 "errors_count", "warnings_count", "manifest_path", "submission_ready_path"]
    )
    summary_path = range_dir / "range_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    logger.info(f"Range summary: {len(rows)} days -> {summary_path}")
