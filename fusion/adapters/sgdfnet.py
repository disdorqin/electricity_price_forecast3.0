from __future__ import annotations

import pandas as pd

from ..contracts import infer_period, standardize_prediction_table
from .base import BasePredictionAdapter


class SGDFNetAdapter(BasePredictionAdapter):
    """Normalize SGDFNet realtime prediction outputs."""

    def load(self) -> pd.DataFrame:
        df = pd.read_csv(self.source)
        out = pd.DataFrame(
            {
                "task": "realtime",
                "model_name": "SGDFNet",
                "target_day": pd.to_datetime(df["target_day"]).dt.strftime("%Y-%m-%d"),
                "ds": pd.to_datetime(df["timestamp"]),
                "hour_business": pd.to_datetime(df["timestamp"]).dt.hour.replace({0: 24}),
                "y_true": df["rt_actual"],
                "y_pred": df["rt_hat"],
            }
        )
        out["period"] = out["hour_business"].map(infer_period)
        return standardize_prediction_table(out)
