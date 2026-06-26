"""
Ledger classifier pipeline.

Runs the negative price classifier on realtime fused predictions.
If the classifier fails, does NOT fail the entire pipeline unless
--strict-classifier is set.

Output:
  outputs/runs/{D}/realtime/final/
    realtime_final_predictions.csv
    realtime_final_predictions_corrected.csv
    classifier_report.json
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def run_ledger_classifier(args: Any) -> dict:
    """
    Main entry for --pipeline ledger_classifier.

    Parameters
    ----------
    args : argparse.Namespace
        Must contain: date, runs_root.
        Optional: strict_classifier, clf_data.

    Returns
    -------
    dict with classifier manifest.
    """
    target_date = args.date
    if not target_date:
        raise ValueError("--date is required for ledger_classifier")

    runs_root = Path(getattr(args, "runs_root", "outputs/runs"))
    strict = getattr(args, "strict_classifier", False)

    logger.info(f"=== ledger_classifier: {target_date} ===")

    manifest = {
        "pipeline": "ledger_classifier",
        "target_date": target_date,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "results": {},
        "warnings": [],
        "errors": [],
    }

    realtime_final_dir = runs_root / target_date / "realtime" / "final"
    realtime_final_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Read fused predictions
        fused_path = runs_root / target_date / "realtime" / "fuse" / "fused_predictions.csv"

        if not fused_path.exists():
            raise FileNotFoundError(f"Fused predictions not found: {fused_path}")

        fused_df = pd.read_csv(fused_path)

        # First, copy fused as uncorrected final
        fused_df.to_csv(realtime_final_dir / "realtime_final_predictions.csv", index=False)
        manifest["results"]["uncorrected_rows"] = len(fused_df)

        # Try running classifier
        classifier_result = _run_extreme_price_classifier(
            fused_df=fused_df,
            target_date=target_date,
            runs_root=runs_root,
            args=args,
        )

        if classifier_result["success"]:
            # Save corrected predictions
            corrected_df = classifier_result.get("corrected_df")
            if corrected_df is not None:
                corrected_df.to_csv(
                    realtime_final_dir / "realtime_final_predictions_corrected.csv",
                    index=False,
                )
                manifest["results"]["corrected_rows"] = len(corrected_df)
                manifest["results"]["corrections_applied"] = classifier_result.get("n_corrections", 0)

            manifest["status"] = "complete"
        else:
            msg = f"Classifier failed: {classifier_result.get('error', 'unknown')}"
            if strict:
                manifest["status"] = "failed"
                manifest["errors"].append(msg)
            else:
                manifest["status"] = "complete_with_warnings"
                manifest["warnings"].append(msg + " (uncorrected predictions saved)")

        # Save classifier report
        report = {
            "target_date": target_date,
            "method": classifier_result.get("method", "unknown"),
            "success": classifier_result["success"],
            "fallback_used": classifier_result.get("fallback_used", False),
            "error": classifier_result.get("error"),
            "original_error": classifier_result.get("original_error"),
            "n_corrections": classifier_result.get("n_corrections", 0),
            "n_corrected_rows": manifest["results"].get("corrected_rows", 0),
        }
        with open(realtime_final_dir / "classifier_report.json", "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        manifest["status"] = "failed" if strict else "complete_with_warnings"
        manifest["errors" if strict else "warnings"].append(str(e))
        logger.exception(f"ledger_classifier error: {e}")

    # Write manifest
    manifest_path = runs_root / target_date / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        with open(manifest_path, "r") as f:
            existing = json.load(f)
        existing["classifier_stage"] = manifest
        with open(manifest_path, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False, default=str)
    else:
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

    return manifest


def _run_extreme_price_classifier(
    fused_df: pd.DataFrame,
    target_date: str,
    runs_root: Path,
    args: Any = None,
) -> dict:
    """
    Run the negative price classifier on fused realtime predictions.

    Tries to use the existing ExtremePriceClf module via classifier_bridge.
    Falls back to a simple threshold-based classifier if the module
    is not available.
    """
    result = {"success": False, "n_corrections": 0, "method": "unknown"}

    try:
        from fusion.classifier_bridge import run_classifier_pipeline

        logger.info("Using fusion.classifier_bridge for classification")

        # Setup compat fusion directory for classifier_bridge
        compat_work_dir = runs_root / target_date / "realtime" / "compat_fusion"
        rt_fused_dir = compat_work_dir / "realtime"
        rt_fused_dir.mkdir(parents=True, exist_ok=True)

        # Copy fused predictions to where bridge expects them
        fused_df.to_csv(rt_fused_dir / "fused_predictions.csv", index=False)

        # Resolve clf_data_path
        clf_data = None
        if args is not None:
            clf_data = getattr(args, "clf_data", None) or getattr(args, "data_path", None)
        if clf_data is None:
            clf_data = "data/shandong_pmos_hourly.xlsx"

        # Call bridge with correct signature
        clf_result = run_classifier_pipeline(
            fusion_work_dir=compat_work_dir,
            project_root=Path.cwd(),
            start_date=target_date,
            end_date=target_date,
            clf_data_path=Path(clf_data),
        )

        if clf_result is not None and clf_result.get("status") == "completed":
            # Load corrected predictions
            corrected_path = rt_fused_dir / "fused_predictions_corrected.csv"
            if corrected_path.exists():
                corrected_df = pd.read_csv(corrected_path)
                result["success"] = True
                result["method"] = "classifier_bridge"
                result["corrected_df"] = corrected_df

                # Count corrections
                if "y_fused" in fused_df.columns and "y_fused_corrected" in corrected_df.columns:
                    corrections = int(
                        (fused_df["y_fused"].values != corrected_df["y_fused_corrected"].values).sum()
                    )
                    result["n_corrections"] = corrections
            else:
                result["error"] = "Bridge completed but no corrected file produced"
        elif clf_result is not None and clf_result.get("status") == "skipped":
            result["error"] = clf_result.get("reason", "Classifier data doesn't cover date range")
            result["fallback_used"] = True
        else:
            result["error"] = f"Bridge returned: {clf_result}"
            result["fallback_used"] = True

    except ImportError:
        logger.warning("classifier_bridge not available, using fallback")
        result = _fallback_classifier(fused_df, target_date)
        result["fallback_used"] = True
    except Exception as e:
        logger.warning(f"Classifier failed, using fallback: {e}")
        result = _fallback_classifier(fused_df, target_date)
        result["original_error"] = str(e)
        result["fallback_used"] = True

    return result


def _fallback_classifier(fused_df: pd.DataFrame, target_date: str) -> dict:
    """
    Simple threshold-based negative price classifier.

    This is a fallback when the full ExtremePriceClf module is not
    available or fails.
    """
    df = fused_df.copy()
    df["y_fused_corrected"] = df["y_fused"].copy()

    # Simple rule: if y_fused < -50 (very negative), flag as extreme
    extreme_mask = df["y_fused"] < -50
    n_extreme = extreme_mask.sum()

    if n_extreme > 0:
        # Correct extreme negative prices to -80 (standard correction)
        df.loc[extreme_mask, "y_fused_corrected"] = -80.0
        logger.info(
            f"Fallback classifier: {n_extreme} hours flagged as extreme negative, "
            f"corrected to -80"
        )

    return {
        "success": True,
        "method": "fallback_threshold",
        "fallback_used": True,
        "corrected_df": df,
        "n_corrections": int(n_extreme),
    }
