from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def classifier_data_covers_range(clf_data_path: Path, start_date: str, end_date: str) -> tuple[bool, str]:
    if not clf_data_path.exists():
        return False, f"classifier data file not found: {clf_data_path}"
    try:
        df = pd.read_excel(clf_data_path, usecols=["时刻"], engine="openpyxl")
    except Exception as exc:  # noqa: BLE001
        return False, f"failed to read classifier data range: {exc}"
    if "时刻" not in df.columns:
        return False, "classifier data missing 时刻 column"
    ts = pd.to_datetime(df["时刻"], errors="coerce").dropna()
    if ts.empty:
        return False, "classifier data has no valid 时刻 values"
    required_start = pd.Timestamp(start_date) + pd.Timedelta(hours=1)
    required_end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    data_start = ts.min()
    data_end = ts.max()
    if data_start <= required_start and data_end >= required_end:
        return True, ""
    return (
        False,
        f"classifier data covers {data_start} ~ {data_end}, required {required_start} ~ {required_end}",
    )


def convert_fusion_to_clf_input(fused_csv_path: Path, output_xlsx_path: Path) -> Path:
    df = pd.read_csv(fused_csv_path)
    if "ds" not in df.columns or "y_fused" not in df.columns:
        raise ValueError(f"Fusion output missing required columns: {fused_csv_path}")
    frame = df.copy()
    frame["ds"] = pd.to_datetime(frame["ds"], errors="coerce")
    frame = frame.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)
    out = pd.DataFrame(
        {
            "时刻": frame["ds"],
            "预测实时电价": frame["y_fused"],
        }
    )
    if "y_true" in frame.columns:
        out["实时电价"] = frame["y_true"]
    output_xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_excel(output_xlsx_path, index=False)
    return output_xlsx_path


def run_extreme_price_classifier(
    *,
    project_root: Path,
    start_date: str,
    end_date: str,
    clf_input_path: Path,
    clf_data_path: Path,
    output_dir: Path,
) -> Path:
    script_path = project_root / "ExtremPriceClf" / "merge_model_scripts" / "run_daily.py"
    if not script_path.exists():
        raise FileNotFoundError(f"Classifier script not found: {script_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Always pass absolute paths — run_daily.py resolves relative paths
    # against its own project_root (ExtremPriceClf/), not our cwd.
    abs_output_dir = output_dir.resolve()
    abs_data_path = clf_data_path.resolve()
    cmd = [
        sys.executable,
        str(script_path),
        start_date,
        end_date,
        "--output",
        str(abs_output_dir),
        "--data",
        str(abs_data_path),
    ]
    if clf_input_path.exists():
        temp_copy = clf_input_path
        target_copy = project_root / "ExtremPriceClf" / "data" / "融合模型预测电价数据.xlsx"
        shutil.copyfile(temp_copy, target_copy)
    subprocess.run(cmd, check=True, cwd=script_path.parent.parent)
    result_path = output_dir / f"{start_date}_{end_date}_clf.xlsx"
    if not result_path.exists():
        raise FileNotFoundError(f"Classifier result not found: {result_path}")
    return result_path


def merge_clf_results(fused_csv_path: Path, clf_result_path: Path, output_path: Path) -> pd.DataFrame:
    fused = pd.read_csv(fused_csv_path)
    clf = pd.read_excel(clf_result_path, engine="openpyxl")
    clf = clf.rename(columns={"时刻": "ds"})
    clf["ds"] = pd.to_datetime(clf["ds"], errors="coerce")
    fused["ds"] = pd.to_datetime(fused["ds"], errors="coerce")
    merged = fused.merge(clf[["ds", "final_pred"]], on="ds", how="left")
    merged["y_fused_corrected"] = merged["y_fused"]
    mask = (merged["final_pred"] == 1) & (merged["y_fused"] <= 100)
    merged.loc[mask, "y_fused_corrected"] = -80.0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False, encoding="utf-8-sig")
    return merged


def run_classifier_pipeline(
    *,
    fusion_work_dir: Path,
    project_root: Path,
    start_date: str,
    end_date: str,
    clf_data_path: Path,
) -> dict:
    rt_fused = fusion_work_dir / "realtime" / "fused_predictions.csv"
    if not rt_fused.exists():
        return {"status": "skipped", "reason": "missing_rt_fused"}
    covered, reason = classifier_data_covers_range(clf_data_path, start_date, end_date)
    if not covered:
        return {"status": "skipped", "reason": reason, "clf_data_path": str(clf_data_path)}
    clf_dir = fusion_work_dir / "classifier"
    clf_dir.mkdir(parents=True, exist_ok=True)
    clf_input = clf_dir / "clf_input.xlsx"
    convert_fusion_to_clf_input(rt_fused, clf_input)
    clf_result = run_extreme_price_classifier(
        project_root=project_root,
        start_date=start_date,
        end_date=end_date,
        clf_input_path=clf_input,
        clf_data_path=clf_data_path,
        output_dir=clf_dir,
    )
    corrected = fusion_work_dir / "realtime" / "fused_predictions_corrected.csv"
    merged = merge_clf_results(rt_fused, clf_result, corrected)
    return {
        "status": "completed",
        "corrected_hours": int((merged["final_pred"] == 1).sum()),
        "output_path": str(corrected),
        "clf_result_path": str(clf_result),
        "clf_input_path": str(clf_input),
    }
