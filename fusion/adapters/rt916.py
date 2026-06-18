from __future__ import annotations

import pandas as pd

from ..contracts import infer_period, standardize_prediction_table
from .base import BasePredictionAdapter


class RT916Adapter(BasePredictionAdapter):
    """Normalize RT916 day-ahead or realtime prediction outputs."""

    def __init__(self, source: str, *, task: str):
        super().__init__(source)
        self.task = task

    def load(self) -> pd.DataFrame:
        df = pd.read_csv(self.source)

        if self.task == "dayahead":
            ds_candidates = ["时刻", "鏃跺埢", "ds"]
            y_true_candidates = ["日前电价", "鏃ュ墠鐢典环", "y_true"]
            y_pred_candidates = ["预测日前电价", "棰勬祴鏃ュ墠鐢典环", "y_pred"]
        else:
            ds_candidates = ["时刻", "鏃跺埢", "ds"]
            y_true_candidates = ["实时电价", "瀹炴椂鐢典环", "y_true"]
            y_pred_candidates = ["预测实时电价", "棰勬祴瀹炴椂鐢典环", "y_pred"]

        def pick_column(candidates: list[str]) -> str:
            for column in candidates:
                if column in df.columns:
                    return column
            raise KeyError(
                f"RT916 output missing required columns. candidates={candidates}, available={list(df.columns)}"
            )

        ds_col = pick_column(ds_candidates)
        y_true_col = pick_column(y_true_candidates)
        y_pred_col = pick_column(y_pred_candidates)

        ds = pd.to_datetime(df[ds_col])
        hour_business = ds.dt.hour.replace({0: 24})
        target_day = ds.dt.normalize()
        target_day = target_day.where(ds.dt.hour != 0, target_day - pd.Timedelta(days=1))

        out = pd.DataFrame(
            {
                "task": self.task,
                "model_name": "RT916_SpikeFusionNet",
                "target_day": target_day.dt.strftime("%Y-%m-%d"),
                "ds": ds,
                "hour_business": hour_business,
                "y_true": pd.to_numeric(df[y_true_col], errors="coerce"),
                "y_pred": pd.to_numeric(df[y_pred_col], errors="coerce"),
            }
        )
        out["period"] = out["hour_business"].map(infer_period)
        return standardize_prediction_table(out)
