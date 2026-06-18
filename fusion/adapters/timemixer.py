from __future__ import annotations

import pandas as pd

from ..contracts import infer_period, standardize_prediction_table
from .base import BasePredictionAdapter


class TimeMixerAdapter(BasePredictionAdapter):
    """Normalize TimeMixer export files into the fusion long-table contract."""

    def __init__(self, source: str, *, task: str):
        super().__init__(source)
        self.task = task

    def load(self) -> pd.DataFrame:
        df = pd.read_csv(self.source)
        if "official_test" in df.columns:
            official = df[df["official_test"] == True].copy()
            if not official.empty:
                df = official
        if "test_window_complete" in df.columns:
            complete = df[df["test_window_complete"] == True].copy()
            if not complete.empty:
                df = complete

        if self.task == "dayahead":
            rename_map = {
                "pred_day_ahead_price": "y_pred",
                "day_ahead_clearing_price": "y_true",
            }
        else:
            rename_map = {
                "pred_realtime_price": "y_pred",
                "realtime_price": "y_true",
            }

        out = df.rename(columns=rename_map).copy()
        if "target_day" in out.columns and "hour_business" in out.columns:
            target_day = pd.to_datetime(out["target_day"], errors="coerce")
            hour_business = pd.to_numeric(out["hour_business"], errors="coerce")
            # Rebuild ds from the business day to align the midnight row with
            # other adapters. In these exports, hour 24 should map to next-day
            # 00:00 rather than same-day 00:00.
            out["ds"] = target_day + pd.to_timedelta(hour_business.mod(24), unit="h")
            out.loc[hour_business == 24, "ds"] = target_day.loc[hour_business == 24] + pd.Timedelta(days=1)
        out["task"] = self.task
        out["model_name"] = "TimeMixer"
        # TimeMixer exports custom labels such as peak/solar/valley in the raw file.
        # The fusion contract only accepts business-hour segments, so always
        # normalize period from hour_business here instead of trusting the source.
        out["period"] = out["hour_business"].map(infer_period)
        out = out.drop_duplicates(subset=["target_day", "ds", "hour_business"], keep="first")
        return standardize_prediction_table(
            out[["task", "model_name", "target_day", "ds", "period", "hour_business", "y_true", "y_pred"]]
        )
