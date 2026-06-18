from __future__ import annotations

from pathlib import Path

import pandas as pd


def ensure_runtime_dirs(root: str | Path) -> Path:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    return root_path


def ensure_prediction_frame(frame: pd.DataFrame, prediction_col: str | None = None) -> pd.DataFrame:
    if "时刻" in frame.columns:
        out = frame.copy()
    elif "timestamp" in frame.columns:
        out = frame.rename(columns={"timestamp": "时刻"}).copy()
    elif "ds" in frame.columns:
        out = frame.rename(columns={"ds": "时刻"}).copy()
    elif "鏃跺埢" in frame.columns:
        out = frame.rename(columns={"鏃跺埢": "时刻"}).copy()
    else:
        first_col = frame.columns[0] if len(frame.columns) > 0 else None
        if first_col is None:
            raise ValueError("Prediction frame is empty.")
        out = frame.rename(columns={first_col: "时刻"}).copy()

    resolved_pred_col = prediction_col
    if resolved_pred_col is None or resolved_pred_col not in out.columns:
        pred_candidates = [col for col in out.columns if col != "时刻"]
        if not pred_candidates:
            raise ValueError("Prediction frame has no prediction column.")
        resolved_pred_col = pred_candidates[0]

    result = out[["时刻", resolved_pred_col]].copy()
    result.columns = ["时刻", "prediction"]
    result["时刻"] = pd.to_datetime(result["时刻"])
    return result.sort_values("时刻").reset_index(drop=True)
