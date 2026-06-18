from __future__ import annotations

import os
from pathlib import Path
import sys

import pandas as pd

from pipelines.base import BaseModelPipeline, PredictionResult
from utils.io import ensure_prediction_frame, ensure_runtime_dirs


SRC_ROOT = Path(__file__).resolve().parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from rt916_spikefusionnet import core  # noqa: E402


TARGET_MAP = {
    "dayahead": "日前电价",
    "realtime": "实时电价",
}


class ModelPipeline(BaseModelPipeline):
    model_name = "rt916"
    device_type = "gpu"

    def train(self, target: str = "realtime", **kwargs):
        start_end = self._resolve_start_end(kwargs)
        return core.train_interface(target=TARGET_MAP[target], start_end_list=start_end, mod="all")

    def predict(self, **kwargs) -> PredictionResult:
        return self.predict_range(**kwargs)

    def predict_range(self, target: str, **kwargs) -> PredictionResult:
        os.environ["SPIKE_TRAIN_MONTHS"] = str(int(kwargs.get("training_months", 12)))
        start_end = self._resolve_start_end(kwargs)
        if target == "realtime":
            # RT916 realtime must first produce DA predictions, then inject them into RT.
            result = core.run_joint_da_rt_daily_backtest(
                start_end_list=start_end,
                mod="all",
                asof_hour=15,
            )
        else:
            result = core.run_daily_asof_backtest(
                target=TARGET_MAP[target],
                start_end_list=start_end,
                mod="all",
                asof_hour=15,
                retrain_daily=False,
            )
        prediction_col = "预测日前电价" if target == "dayahead" else "预测实时电价"
        normalized = ensure_prediction_frame(result, prediction_col)
        output_root = ensure_runtime_dirs(Path(kwargs.get("output_root", "outputs/unified_runs")) / self.model_name / target)
        output_path = output_root / "predictions.csv"
        normalized.to_csv(output_path, index=False, encoding="utf-8-sig")
        return PredictionResult(model_name=self.model_name, target=target, output_path=output_path, frame=normalized)

    @staticmethod
    def _resolve_start_end(kwargs: dict) -> list[str]:
        start = kwargs.get("start")
        end = kwargs.get("end")
        if start and end:
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)
            if start_ts.hour == 0 and start_ts.minute == 0 and start_ts.second == 0:
                start_ts = start_ts.normalize() + pd.Timedelta(hours=1)
            if end_ts.hour == 0 and end_ts.minute == 0 and end_ts.second == 0:
                end_ts = end_ts.normalize() + pd.Timedelta(days=1)
            return [start_ts.strftime("%Y-%m-%d %H:%M:%S"), end_ts.strftime("%Y-%m-%d %H:%M:%S")]
        predict_date = pd.Timestamp(kwargs.get("predict_date"))
        start_ts = predict_date.normalize() + pd.Timedelta(hours=1)
        end_ts = predict_date.normalize() + pd.Timedelta(days=1)
        return [start_ts.strftime("%Y-%m-%d %H:%M:%S"), end_ts.strftime("%Y-%m-%d %H:%M:%S")]
