from __future__ import annotations

import pandas as pd

from ..contracts import infer_period, standardize_prediction_table
from .base import BasePredictionAdapter


class LightGBMAdapter(BasePredictionAdapter):
    """Normalize LightGBM prediction outputs."""

    def __init__(self, source: str, *, task: str):
        super().__init__(source)
        self.task = task

    def load(self) -> pd.DataFrame:
        df = pd.read_csv(self.source)
        ds = pd.to_datetime(df["ds"])
        hour_business = pd.to_numeric(df["hour"], errors="coerce").astype(int)

        out = pd.DataFrame(
            {
                "task": self.task,
                "model_name": "lightGBM",
                "target_day": df.get("target_day", ds.dt.strftime("%Y-%m-%d")),
                "ds": ds,
                "hour_business": hour_business,
                "y_true": pd.to_numeric(df["y"], errors="coerce"),
                "y_pred": pd.to_numeric(df["pred_y"], errors="coerce"),
            }
        )
        out["period"] = out["hour_business"].map(infer_period)
        return standardize_prediction_table(out)
