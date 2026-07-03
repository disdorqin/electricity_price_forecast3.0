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

                # Build corrected_hours detail
                corrected_hours = classifier_result.get("corrected_hours", [])
                if not corrected_hours and classifier_result.get("n_corrections", 0) > 0 and "y_fused" in fused_df.columns and "y_fused_corrected" in corrected_df.columns:
                    corrected_hours = _build_corrected_hours(fused_df, corrected_df)
                manifest["results"]["corrected_hours"] = corrected_hours or []

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
            "corrected_hours": manifest["results"].get("corrected_hours", []),
        }
        with open(realtime_final_dir / "classifier_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        # Write -80_prob.csv with classifier probabilities
        _write_classifier_prob_csv(
            classifier_result=classifier_result,
            fused_df=fused_df,
            target_date=target_date,
            runs_root=runs_root,
            realtime_final_dir=realtime_final_dir,
            manifest=manifest,
        )

        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        manifest["status"] = "failed" if strict else "complete_with_warnings"
        manifest["errors" if strict else "warnings"].append(str(e))
        logger.exception(f"ledger_classifier error: {e}")

    # Write manifest
    manifest_path = runs_root / target_date / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing["classifier_stage"] = manifest
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False, default=str)
    else:
        with open(manifest_path, "w", encoding="utf-8") as f:
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

    Uses the ExtremePriceClf module via classifier_bridge.
    On failure, reports error only — no fallback correction.
    """
    result: dict[str, Any] = {"success": False, "n_corrections": 0, "method": "unknown"}

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

                # Count corrections via bridge result or compute
                corrections = clf_result.get("n_corrections", 0)
                if corrections == 0 and "y_fused" in fused_df.columns and "y_fused_corrected" in corrected_df.columns:
                    corrections = int(
                        (fused_df["y_fused"].values != corrected_df["y_fused_corrected"].values).sum()
                    )
                result["n_corrections"] = corrections

                # Build corrected_hours from bridge result or compute
                corrected_hours = clf_result.get("corrected_hours", [])
                if not corrected_hours and corrections > 0 and "y_fused" in fused_df.columns and "y_fused_corrected" in corrected_df.columns:
                    corrected_hours = _build_corrected_hours(fused_df, corrected_df)
                result["corrected_hours"] = corrected_hours or []
            else:
                result["error"] = "Bridge completed but no corrected file produced"
        elif clf_result is not None and clf_result.get("status") == "skipped":
            reason = clf_result.get("reason", "unknown")
            logger.warning(f"Classifier bridge skipped: {reason}")
            result["error"] = f"Bridge skipped: {reason}"
        else:
            logger.warning(f"Classifier bridge returned unexpected: {clf_result}")
            result["error"] = f"Bridge returned unexpected: {clf_result}"

    except ImportError:
        logger.warning("classifier_bridge not available")
        result["error"] = "classifier_bridge module not available"
    except Exception as e:
        logger.warning(f"Classifier failed: {e}")
        result["error"] = str(e)

    return result


def _write_classifier_prob_csv(
    classifier_result: dict,
    fused_df: pd.DataFrame,
    target_date: str,
    runs_root: Path,
    realtime_final_dir: Path,
    manifest: dict,
):
    """Write -80_prob.csv with classifier probabilities from bridge output."""
    prob_path = realtime_final_dir / "-80_prob.csv"

    if classifier_result.get("method") == "classifier_bridge" and classifier_result.get("success"):
        clf_xlsx = runs_root / target_date / "realtime" / "compat_fusion" / "classifier" / f"{target_date}_{target_date}_clf.xlsx"
        if clf_xlsx.exists():
            try:
                clf_df = pd.read_excel(clf_xlsx, engine="openpyxl")
                if "时刻" in clf_df.columns and "final_prob" in clf_df.columns:
                    prob_df = clf_df[["时刻", "final_prob", "threshold", "final_pred"]].copy()
                    prob_df.to_csv(prob_path, index=False, encoding="utf-8-sig")
                    logger.info(f"-80_prob.csv written from classifier bridge ({len(prob_df)} rows)")
                    manifest["results"]["prob_csv"] = "classifier_bridge"
                    return
            except Exception as e:
                logger.warning(f"Failed to read classifier xlsx for prob CSV: {e}")


def _build_corrected_hours(
    fused_df: pd.DataFrame, corrected_df: pd.DataFrame
) -> list[dict]:
    """Build list of corrected hours with before/after values."""
    corrected_hours = []
    # Align by index; fall back to comparing y_fused vs y_fused_corrected
    common_col = "y_fused_corrected"
    if common_col not in corrected_df.columns:
        return corrected_hours

    fused_vals = fused_df["y_fused"].values
    corrected_vals = corrected_df[common_col].values
    min_len = min(len(fused_vals), len(corrected_vals))
    for i in range(min_len):
        if fused_vals[i] != corrected_vals[i]:
            row_idx = corrected_df.index[i] if i < len(corrected_df) else i
            row = corrected_df.iloc[i]
            corrected_hours.append({
                "hour_business": int(row.get("hour_business", 0)),
                "ds": str(row.get("ds", "")),
                "before": float(fused_vals[i]),
                "after": float(corrected_vals[i]),
            })
    return corrected_hours
