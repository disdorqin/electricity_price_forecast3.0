#!/usr/bin/env python
"""R3D-Tap-GEF 一键运行入口（发布版）。

将四阶段链路拆分为两个独立子进程（DA / RT），避免单进程 GPU OOM。
用法:
    python scripts/run_one_day_release.py 2026-02-01
    python scripts/run_one_day_release.py 2026-02-01 --force
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_one_day_release")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FORMAL_DAYAHEAD_MODELS = ["lightgbm", "timesfm", "timemixer"]
FORMAL_REALTIME_MODELS = ["timesfm", "timemixer", "sgdfnet", "rt916"]


def _output_dir(dt: str) -> Path:
    return PROJECT_ROOT / "outputs" / dt


def _file_nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _confirm_pipeline_status(dt: str) -> dict:
    manifest_file = _output_dir(dt) / "run_manifest.json"
    if not manifest_file.exists():
        return {"status": "not_found"}
    with open(manifest_file, encoding="utf-8") as f:
        return json.load(f)


def _build_base_cmd(date_str: str, args) -> list[str]:
    cmd = [
        sys.executable, "main.py", date_str,
    ]
    if args.force:
        cmd.append("--force")
    if args.timemixer_epochs is not None:
        cmd += ["--timemixer-epochs", str(args.timemixer_epochs)]
    return cmd


def _run_dayahead(date_str: str, args) -> bool:
    cmd = _build_base_cmd(date_str, args) + [
        "--target", "dayahead",
        "--stage-models", ",".join(FORMAL_DAYAHEAD_MODELS),
    ]
    logger.info("=" * 60)
    logger.info("PHASE 1/2: Dayahead pipeline")
    logger.info("Command: %s", " ".join(cmd))
    logger.info("=" * 60)
    t0 = time.time()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.time() - t0
    logger.info("Dayahead: exit=%d, elapsed=%.1fs", result.returncode, elapsed)
    return result.returncode == 0


def _run_realtime(date_str: str, args) -> bool:
    cmd = _build_base_cmd(date_str, args) + [
        "--target", "realtime",
        "--stage-models", ",".join(FORMAL_REALTIME_MODELS),
    ]
    logger.info("=" * 60)
    logger.info("PHASE 2/2: Realtime pipeline")
    logger.info("Command: %s", " ".join(cmd))
    logger.info("=" * 60)
    t0 = time.time()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.time() - t0
    logger.info("Realtime: exit=%d, elapsed=%.1fs", result.returncode, elapsed)
    return result.returncode == 0


def _normalize_fused(df: pd.DataFrame, price_col: str) -> pd.DataFrame:
    """Normalize fused output to have standard column names."""
    out = df.copy()
    if "business_day" not in out.columns:
        if "target_day" in out.columns:
            out["business_day"] = out["target_day"]
    if price_col in out.columns:
        out = out.rename(columns={price_col: "price"})
    return out


def _assemble_final_outputs(dt: str):
    """Assemble final outputs from daily_runs/ to outputs/{date}/final/."""
    ddir = _output_dir(dt)
    final_dir = ddir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    daily_root = PROJECT_ROOT / "daily_runs" / dt

    # Dayahead final
    da_src = daily_root / "dayahead" / "final" / "fused_predictions.csv"
    if _file_nonempty(da_src):
        df = pd.read_csv(da_src)
        df = _normalize_fused(df, "y_fused")
        df.to_csv(final_dir / "dayahead_final_predictions.csv", index=False)

    # Realtime corrected (post-classifier)
    rt_corrected_src = daily_root / "realtime" / "final" / "fused_predictions_corrected.csv"
    if _file_nonempty(rt_corrected_src):
        df = pd.read_csv(rt_corrected_src)
        df = _normalize_fused(df, "y_fused_corrected")
        df.to_csv(final_dir / "realtime_final_predictions_corrected.csv", index=False)
        df.to_csv(final_dir / "realtime_final_predictions.csv", index=False)
    else:
        # Fallback: use uncorrected
        rt_fused_src = daily_root / "realtime" / "final" / "fused_predictions.csv"
        if _file_nonempty(rt_fused_src):
            df = pd.read_csv(rt_fused_src)
            df = _normalize_fused(df, "y_fused")
            df.to_csv(final_dir / "realtime_final_predictions.csv", index=False)

    # Realtime fused (pre-classifier, for reference)
    rt_fused_src = daily_root / "realtime" / "final" / "fused_predictions.csv"
    if _file_nonempty(rt_fused_src):
        shutil.copy2(rt_fused_src, final_dir / "realtime_fused_predictions.csv")

    # Classifier report
    clf_src = daily_root / "realtime" / "final" / "classifier_report.json"
    if _file_nonempty(clf_src):
        shutil.copy2(clf_src, final_dir / "classifier_report.json")

    # Build submission_ready.csv
    _build_submission_ready(final_dir)
    logger.info("Final outputs assembled in %s", final_dir)


def _build_submission_ready(final_dir: Path):
    """Build submission_ready.csv from final outputs."""
    da_file = final_dir / "dayahead_final_predictions.csv"
    rt_file = final_dir / "realtime_final_predictions.csv"

    da_df = None
    rt_df = None

    if _file_nonempty(da_file):
        da_df = pd.read_csv(da_file)
        da_df["dayahead_price"] = da_df.get("price", da_df.get("y_fused", None))

    if _file_nonempty(rt_file):
        rt_df = pd.read_csv(rt_file)
        price_col = "price" if "price" in rt_df.columns else ("y_fused_corrected" if "y_fused_corrected" in rt_df.columns else "y_fused")
        rt_df["realtime_price"] = rt_df.get(price_col, None)

    if da_df is None and rt_df is None:
        logger.warning("No data for submission_ready.csv")
        return

    FIXED_COLUMNS = ["business_day", "ds", "hour_business", "period", "dayahead_price", "realtime_price"]

    if da_df is not None and rt_df is not None:
        merge_keys = [k for k in ["business_day", "hour_business", "ds", "period"] if k in da_df.columns and k in rt_df.columns]
        if not merge_keys:
            merge_keys = [k for k in ["business_day", "hour_business"] if k in da_df.columns and k in rt_df.columns]
        da_sub = da_df[merge_keys + ["dayahead_price"]].copy() if "dayahead_price" in da_df.columns else da_df
        rt_sub = rt_df[merge_keys + ["realtime_price"]].copy() if "realtime_price" in rt_df.columns else rt_df
        submission = da_sub.merge(rt_sub, on=merge_keys, how="outer")
        for col in list(submission.columns):
            if col.endswith("_x") or col.endswith("_y"):
                submission = submission.drop(columns=[col])
    elif da_df is not None:
        submission = da_df[FIXED_COLUMNS].copy() if all(c in da_df.columns for c in FIXED_COLUMNS) else da_df
        if "realtime_price" not in submission.columns:
            submission["realtime_price"] = None
    else:
        submission = rt_df[FIXED_COLUMNS].copy() if all(c in rt_df.columns for c in FIXED_COLUMNS) else rt_df
        if "dayahead_price" not in submission.columns:
            submission["dayahead_price"] = None

    out_cols = [c for c in FIXED_COLUMNS if c in submission.columns]
    submission = submission[out_cols]
    submission.to_csv(final_dir / "submission_ready.csv", index=False)
    logger.info("submission_ready.csv created with %d rows", len(submission))


def _write_manifest(dt: str, status: str, stages: dict, warnings: list[str], errors: list[str]):
    ddir = _output_dir(dt)
    ddir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "date": dt,
        "status": status,
        "pipeline_version": "r3d_tap_gef_v1",
        "started_at": None,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "targets": ["dayahead", "realtime"],
        "dayahead_models": FORMAL_DAYAHEAD_MODELS,
        "realtime_models": FORMAL_REALTIME_MODELS,
        "steps": stages,
        "final_outputs": {},
        "warnings": warnings,
        "gate_messages": errors,
    }
    with open(ddir / "run_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description="R3D-Tap-GEF Release — split-process single-day pipeline"
    )
    parser.add_argument("date", nargs="?", default=None, help="Target date YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Force rerun")
    parser.add_argument("--timemixer-epochs", type=int, default=None, help="TimeMixer training epochs (default: 80)")
    parser.add_argument("--skip-da", action="store_true", help="Skip dayahead (only realtime)")
    parser.add_argument("--skip-rt", action="store_true", help="Skip realtime (only dayahead)")
    args = parser.parse_args()

    date_str = args.date
    if date_str is None:
        date_str = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).strftime("%Y-%m-%d")
        logger.info("No date specified, using: %s", date_str)

    date_str = pd.Timestamp(date_str).strftime("%Y-%m-%d")
    t_start = time.time()
    warnings: list[str] = []
    errors: list[str] = []
    stages: dict[str, str] = {}
    da_ok = True
    rt_ok = True

    if not args.skip_da:
        da_ok = _run_dayahead(date_str, args)
        stages["dayahead"] = "complete" if da_ok else "failed"
        if not da_ok:
            errors.append("Dayahead pipeline failed")
    else:
        stages["dayahead"] = "skipped"

    if not args.skip_rt:
        rt_ok = _run_realtime(date_str, args)
        stages["realtime"] = "complete" if rt_ok else "failed"
        if not rt_ok:
            errors.append("Realtime pipeline failed")
    else:
        stages["realtime"] = "skipped"

    if da_ok or rt_ok:
        try:
            _assemble_final_outputs(date_str)
            stages["assemble"] = "complete"
        except Exception as e:
            stages["assemble"] = f"failed: {e}"
            warnings.append(f"Final assembly failed: {e}")

    total_elapsed = time.time() - t_start
    logger.info("=" * 60)
    logger.info("RELEASE PIPELINE COMPLETE: %s", date_str)
    logger.info("Total time: %.1fs (%.1f min)", total_elapsed, total_elapsed / 60)
    logger.info("=" * 60)

    if errors:
        final_status = "failed"
    elif warnings:
        final_status = "complete_with_warnings"
    else:
        final_status = "complete"

    _write_manifest(date_str, final_status, stages, warnings, errors)
    logger.info("Manifest: outputs/%s/run_manifest.json", date_str)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
