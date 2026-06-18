from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..contracts import infer_period, standardize_prediction_table
from ..project_defaults import DEFAULTS
from .base import BasePredictionAdapter


TASK_TO_TRUE_COL = {
    "dayahead": "日前电价",
    "realtime": "实时电价",
}


def _pick_column(columns: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _infer_target_day_from_ts(ts: pd.Series) -> pd.Series:
    normalized = ts.dt.normalize()
    return normalized.where(ts.dt.hour != 0, normalized - pd.Timedelta(days=1))


def _load_truth_map(data_path: str | Path, task: str) -> pd.Series:
    path = Path(data_path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        raw = pd.read_excel(path)
    else:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    raw["时刻"] = pd.to_datetime(raw["时刻"])
    truth_col = TASK_TO_TRUE_COL[task]
    return raw.set_index("时刻")[truth_col]


class TimesFMAdapter(BasePredictionAdapter):
    """Normalize TimesFM exported backtest or forecast CSVs."""

    def __init__(self, source: str, *, task: str, data_path: str | None = None):
        super().__init__(source)
        self.task = task
        self.data_path = data_path or str(DEFAULTS.data_xlsx)

    def load(self) -> pd.DataFrame:
        df = pd.read_csv(self.source)
        columns = list(df.columns)
        ts_col = _pick_column(columns, ["时刻", "鏃跺埢"])
        pred_col = _pick_column(columns, ["预测值", "棰勬祴鍊?"])
        true_col = _pick_column(columns, ["真实值", "鐪熷疄鍊?"])
        if ts_col is None or pred_col is None:
            raise ValueError(f"TimesFM CSV missing required timestamp/prediction columns: {columns}")

        ds = pd.to_datetime(df[ts_col], errors="coerce")
        hour_business = ds.dt.hour.replace({0: 24}).astype(int)

        if true_col is not None:
            y_true = pd.to_numeric(df[true_col], errors="coerce")
        else:
            truth_map = _load_truth_map(self.data_path, self.task)
            y_true = pd.to_numeric(pd.Series(ds.map(truth_map), index=df.index), errors="coerce")

        out = pd.DataFrame(
            {
                "task": self.task,
                "model_name": "TimesFM",
                "target_day": _infer_target_day_from_ts(ds).dt.strftime("%Y-%m-%d"),
                "ds": ds,
                "hour_business": hour_business,
                "y_true": y_true,
                "y_pred": pd.to_numeric(df[pred_col], errors="coerce"),
            }
        )
        out["period"] = out["hour_business"].map(infer_period)
        out = out.dropna(subset=["ds", "y_true", "y_pred"]).reset_index(drop=True)
        return standardize_prediction_table(out)
