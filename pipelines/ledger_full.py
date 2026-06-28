"""
Ledger full pipeline.

Orchestrates the complete production chain for a single target day D:

  1. ledger_predict   → run all models, append to prediction ledger
  2. ledger_weight    → learn fusion weights from D-30~D-1 ledger
  3. ledger_fuse      → apply weights to produce fused predictions
  4. ledger_classifier → run negative price classifier (realtime)
  5. final outputs    → aggregate dayahead + realtime final files

This is the production-grade replacement for the old staged `full` pipeline.
No validation tap, no rolling OOF, no online validation.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def prepare_daily_run_dir(runs_root: Path, target_date: str, force: bool = False) -> Path:
    """Prepare a clean daily run directory.

    Creates the run directory if it doesn't exist. When ``force`` is True,
    removes any existing content inside ``runs_root/{target_date}/`` first.

    **Safety**: never touches ``outputs/ledger/``, ``outputs/runs/range_*``,
    or any path outside the single-day run directory.
    """
    run_dir = runs_root / target_date
    if force and run_dir.exists():
        logger.info(f"Force mode: clearing existing run directory {run_dir}")
        for child in run_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def run_ledger_full(args: Any) -> dict:
    """
    Main entry for --pipeline ledger_full.

    Parameters
    ----------
    args : argparse.Namespace
        Must contain: date, data_path, epf_v1_root (optional),
        ledger_root, runs_root, max_cpu_workers, max_gpu_workers,
        allow_missing_models, force, strict_classifier.

    Returns
    -------
    dict with full pipeline manifest.
    """
    target_date = args.date
    if not target_date:
        raise ValueError("--date is required for ledger_full")

    logger.info(f"=== ledger_full: {target_date} ===")

    runs_root = Path(getattr(args, "runs_root", "outputs/runs"))
    force = getattr(args, "force", False)

    # Prepare (or optionally clear) the run directory before starting
    prepare_daily_run_dir(runs_root, target_date, force=force)

    manifest = {
        "pipeline": "ledger_full",
        "target_date": target_date,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "stages": {},
        "warnings": [],
        "errors": [],
    }

    failed_early = False

    # -----------------------------------------------------------------------
    # Stage 1: ledger_predict
    # -----------------------------------------------------------------------
    logger.info(f"\n{'='*60}\nStage 1/5: ledger_predict\n{'='*60}")
    try:
        from pipelines.ledger_predict import run_ledger_predict
        predict_result = run_ledger_predict(args)
        manifest["stages"]["ledger_predict"] = predict_result

        if predict_result.get("status") == "failed":
            manifest["status"] = "failed"
            manifest["errors"].append("ledger_predict failed")
            failed_early = True
    except Exception as e:
        manifest["stages"]["ledger_predict"] = {"status": "error", "error": str(e)}
        manifest["errors"].append(f"ledger_predict: {e}")
        manifest["status"] = "failed"
        failed_early = True

    if failed_early:
        _write_manifest(runs_root, target_date, manifest)
        return manifest

    # -----------------------------------------------------------------------
    # Stage 2: ledger_weight
    # -----------------------------------------------------------------------
    logger.info(f"\n{'='*60}\nStage 2/5: ledger_weight\n{'='*60}")
    try:
        from pipelines.ledger_weight import run_ledger_weight
        weight_result = run_ledger_weight(args)
        manifest["stages"]["ledger_weight"] = weight_result

        if weight_result.get("status") == "failed":
            manifest["status"] = "failed"
            manifest["errors"].append("ledger_weight failed")
            failed_early = True
    except Exception as e:
        manifest["stages"]["ledger_weight"] = {"status": "error", "error": str(e)}
        manifest["errors"].append(f"ledger_weight: {e}")
        manifest["status"] = "failed"
        failed_early = True

    if failed_early:
        _write_manifest(runs_root, target_date, manifest)
        return manifest

    # -----------------------------------------------------------------------
    # Stage 3: ledger_fuse
    # -----------------------------------------------------------------------
    logger.info(f"\n{'='*60}\nStage 3/5: ledger_fuse\n{'='*60}")
    try:
        from pipelines.ledger_fuse import run_ledger_fuse
        fuse_result = run_ledger_fuse(args)
        manifest["stages"]["ledger_fuse"] = fuse_result

        if fuse_result.get("status") == "failed":
            manifest["status"] = "failed"
            manifest["errors"].append("ledger_fuse failed")
            failed_early = True
    except Exception as e:
        manifest["stages"]["ledger_fuse"] = {"status": "error", "error": str(e)}
        manifest["errors"].append(f"ledger_fuse: {e}")
        manifest["status"] = "failed"
        failed_early = True

    if failed_early:
        _write_manifest(runs_root, target_date, manifest)
        return manifest

    # -----------------------------------------------------------------------
    # Stage 4: ledger_classifier
    # -----------------------------------------------------------------------
    logger.info(f"\n{'='*60}\nStage 4/5: ledger_classifier\n{'='*60}")
    strict_clf = getattr(args, "strict_classifier", False)
    try:
        from pipelines.ledger_classifier import run_ledger_classifier
        clf_result = run_ledger_classifier(args)
        manifest["stages"]["ledger_classifier"] = clf_result

        clf_status = clf_result.get("status")
        if clf_status == "failed":
            manifest["errors"].append("ledger_classifier failed")
            if strict_clf:
                manifest["status"] = "failed"
                manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
                _write_manifest(runs_root, target_date, manifest)
                return manifest
        else:
            # Propagate classifier warnings/errors to top-level manifest
            for w in clf_result.get("warnings", []):
                manifest["warnings"].append(f"ledger_classifier: {w}")
            for e in clf_result.get("errors", []):
                if strict_clf:
                    manifest["errors"].append(f"ledger_classifier: {e}")
                else:
                    manifest["warnings"].append(f"ledger_classifier: {e}")
    except Exception as e:
        manifest["stages"]["ledger_classifier"] = {"status": "error", "error": str(e)}
        if strict_clf:
            manifest["errors"].append(f"ledger_classifier: {e}")
            manifest["status"] = "failed"
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            _write_manifest(runs_root, target_date, manifest)
            return manifest
        else:
            manifest["warnings"].append(f"ledger_classifier: {e}")

    # -----------------------------------------------------------------------
    # Stage 5: Final outputs
    # -----------------------------------------------------------------------
    logger.info(f"\n{'='*60}\nStage 5/5: Final outputs\n{'='*60}")
    try:
        final_result = _collect_final_outputs(runs_root, target_date)
        manifest["stages"]["final_outputs"] = final_result
    except Exception as e:
        manifest["stages"]["final_outputs"] = {"status": "error", "error": str(e)}
        manifest["warnings"].append(f"final_outputs: {e}")

    # -----------------------------------------------------------------------
    # Determine final status
    # -----------------------------------------------------------------------
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()

    errors = manifest.get("errors", [])
    warnings = manifest.get("warnings", [])

    if errors:
        manifest["status"] = "failed"
    elif warnings:
        manifest["status"] = "complete_with_warnings"
    else:
        manifest["status"] = "complete"

    # -----------------------------------------------------------------------
    # Postflight — validate submission, fallback, next-day readiness
    # -----------------------------------------------------------------------
    from pipelines.delivery_quality import (
        validate_daily_submission,
        validate_next_day_readiness,
    )
    from pipelines.emergency_fallback import try_emergency_fallback
    from pipelines.delivery_report import (
        write_daily_delivery_report,
        print_daily_delivery_report,
    )

    ledger_root = Path(getattr(args, "ledger_root", "outputs/ledger"))
    data_path = getattr(args, "data_path", "data/shandong_pmos_hourly.xlsx")

    # First postflight attempt
    postflight_result = validate_daily_submission(runs_root, target_date)
    manifest["postflight"] = postflight_result

    if postflight_result["status"] == "PASS":
        manifest["delivery_status"] = "NORMAL"
        manifest["fallback"] = {"fallback_used": False}
    else:
        logger.warning(
            f"Postflight FAILED for {target_date}: "
            f"{len(postflight_result['errors'])} error(s). "
            "Attempting emergency fallback..."
        )

        # Try emergency fallback
        fb_args = (target_date, data_path, runs_root)
        fb_reason = (
            f"postflight validation failed: "
            f"{len(postflight_result['errors'])} error(s)"
        )
        fallback_result = try_emergency_fallback(
            target_date, data_path, runs_root, reason=fb_reason,
        )

        if fallback_result["success"]:
            # Re-validate after fallback
            second_postflight = validate_daily_submission(
                runs_root, target_date, allow_degraded=True,
            )
            manifest["postflight"] = second_postflight
            manifest["fallback"] = {
                "fallback_used": True,
                "fallback_method": fallback_result["fallback_method"],
                "fallback_level": fallback_result["fallback_level"],
                "reason": fb_reason,
                "report": fallback_result,
            }

            if second_postflight["status"] == "PASS":
                manifest["delivery_status"] = "DEGRADED_DELIVERED"
            else:
                # Fallback produced output but it still doesn't validate
                manifest["delivery_status"] = "FAILED_NO_DELIVERY"
        else:
            manifest["delivery_status"] = "FAILED_NO_DELIVERY"
            manifest["fallback"] = {
                "fallback_used": True,
                "fallback_method": "historical_same_hour_median",
                "fallback_level": "failed",
                "reason": fb_reason,
                "report": fallback_result,
                "errors": fallback_result.get("errors", []),
            }

    # Next-day readiness
    ndr = validate_next_day_readiness(target_date, ledger_root)
    manifest["next_day_readiness"] = ndr

    # Write delivery report
    _write_manifest(runs_root, target_date, manifest)
    write_daily_delivery_report(runs_root / target_date, manifest)

    # Print terminal report
    print_daily_delivery_report(manifest)

    logger.info(
        f"ledger_full {target_date}: status={manifest['status']}, "
        f"delivery={manifest.get('delivery_status', 'UNSET')}"
    )

    return manifest


def _collect_final_outputs(runs_root: Path, target_date: str) -> dict:
    """Collect and copy final outputs to the top-level final directory."""
    result = {"status": "running"}

    run_dir = runs_root / target_date
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    # Dayahead final — write to BOTH locations
    da_final = run_dir / "dayahead" / "fuse" / "fused_predictions.csv"
    if da_final.exists():
        shutil.copy2(da_final, final_dir / "dayahead_final_predictions.csv")
        # Also write to dayahead/final/
        dayahead_final_dir = run_dir / "dayahead" / "final"
        dayahead_final_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(da_final, dayahead_final_dir / "dayahead_final_predictions.csv")
        da_df = pd.read_csv(da_final)
        result["dayahead_final_rows"] = len(da_df)
        _validate_final(da_df, "dayahead", target_date, result)

    # Realtime final (uncorrected)
    rt_final = run_dir / "realtime" / "final" / "realtime_final_predictions.csv"
    if rt_final.exists():
        shutil.copy2(rt_final, final_dir / "realtime_final_predictions.csv")
        rt_df = pd.read_csv(rt_final)
        result["realtime_final_rows"] = len(rt_df)
        _validate_final(rt_df, "realtime", target_date, result)

    # Realtime final (corrected)
    rt_corrected = run_dir / "realtime" / "final" / "realtime_final_predictions_corrected.csv"
    if rt_corrected.exists():
        shutil.copy2(rt_corrected, final_dir / "realtime_final_predictions_corrected.csv")
        rt_c_df = pd.read_csv(rt_corrected)
        result["realtime_corrected_rows"] = len(rt_c_df)

    # Submission ready
    _build_submission_ready(final_dir, target_date, result)

    result["status"] = "complete"
    return result


def _validate_final(df: pd.DataFrame, task: str, target_date: str, result: dict):
    """Validate final output: 24 rows, hours 1..24, no duplicates."""
    n = len(df)
    if n != 24:
        result.setdefault("warnings", []).append(
            f"{task} final: expected 24 rows, got {n}"
        )

    if "hour_business" in df.columns:
        hours = sorted(df["hour_business"].unique())
        if hours != list(range(1, 25)):
            result.setdefault("warnings", []).append(
                f"{task} final: hours {hours[0]}..{hours[-1]}, "
                f"expected 1..24"
            )

        if df["hour_business"].duplicated().any():
            result.setdefault("warnings", []).append(
                f"{task} final: duplicate hours detected"
            )


def _build_submission_ready(final_dir: Path, target_date: str, result: dict):
    """Build a consolidated submission_ready.csv with dayahead + realtime — fixed columns."""
    da_path = final_dir / "dayahead_final_predictions.csv"
    rt_path = final_dir / "realtime_final_predictions_corrected.csv"

    # Prefer corrected realtime, fall back to uncorrected
    if not rt_path.exists():
        rt_path = final_dir / "realtime_final_predictions.csv"

    if not da_path.exists() and not rt_path.exists():
        result.setdefault("warnings", []).append("No data for submission_ready.csv")
        return

    da_df = None
    rt_df = None

    if da_path.exists():
        da_df = pd.read_csv(da_path)
        da_df = da_df.rename(columns={"y_fused": "dayahead_price"})

    if rt_path.exists():
        rt_df = pd.read_csv(rt_path)
        price_col = "y_fused_corrected" if "y_fused_corrected" in rt_df.columns else "y_fused"
        rt_df = rt_df.rename(columns={price_col: "realtime_price"})

    # Build with fixed, clean columns — merge on business_day + hour_business
    FIXED_COLUMNS = ["business_day", "ds", "hour_business", "period", "dayahead_price", "realtime_price"]

    if da_df is not None and rt_df is not None:
        # Check ds/period consistency before merge
        da_sub = da_df[["business_day", "hour_business", "ds", "period", "dayahead_price"]].copy()
        rt_sub = rt_df[["business_day", "hour_business", "realtime_price"]].copy()
        submission = da_sub.merge(rt_sub, on=["business_day", "hour_business"], how="outer")
        # Drop _x/_y columns if any
        for col in list(submission.columns):
            if col.endswith("_x") or col.endswith("_y"):
                submission = submission.drop(columns=[col])
    elif da_df is not None:
        da_sub = da_df[["business_day", "hour_business", "ds", "period", "dayahead_price"]].copy()
        da_sub["realtime_price"] = None
        submission = da_sub
    else:
        rt_sub = rt_df[["business_day", "hour_business", "ds", "period", "realtime_price"]].copy()
        rt_sub["dayahead_price"] = None
        submission = rt_sub

    # Enforce fixed column order
    out_cols = [c for c in FIXED_COLUMNS if c in submission.columns]
    submission = submission[out_cols]

    submission.to_csv(final_dir / "submission_ready.csv", index=False)
    result["submission_ready_rows"] = len(submission)
    logger.info(f"submission_ready.csv: {len(submission)} rows")


def _write_manifest(runs_root: Path, target_date: str, manifest: dict):
    """Write the final manifest."""
    manifest_path = runs_root / target_date / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)
