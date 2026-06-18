from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipelines.base import BaseModelPipeline, PredictionResult
from utils.io import ensure_prediction_frame, ensure_runtime_dirs

from .main_fix import run_lgbm_pipeline


TARGET_MAP = {
    "dayahead": "日前电价",
    "realtime": "实时电价",
}


class ModelPipeline(BaseModelPipeline):
    model_name = "lightgbm"
    device_type = "cpu"

    def train(self, target: str = "realtime", **kwargs):
        return run_lgbm_pipeline(
            data_path=kwargs["data_path"],
            forecast_start=kwargs["forecast_start"],
            forecast_end=kwargs["forecast_end"],
            target=TARGET_MAP[target],
            use_predicted_temp=bool(kwargs.get("use_predicted_temp", False)),
            training_months=int(kwargs.get("training_months", 12)),
            val_ratio=float(kwargs.get("val_ratio", 0.2)),
        )

    def predict(self, **kwargs) -> PredictionResult:
        return self.predict_range(**kwargs)

    def predict_range(self, target: str, **kwargs) -> PredictionResult:
        data_path = kwargs.get("data_path")
        if not data_path:
            raise ValueError("lightGBM pipeline requires data_path")
        forecast_start, forecast_end = self._resolve_date_window(kwargs)
        result = run_lgbm_pipeline(
            data_path=data_path,
            forecast_start=forecast_start,
            forecast_end=forecast_end,
            target=TARGET_MAP[target],
            use_predicted_temp=bool(kwargs.get("use_predicted_temp", False)),
            training_months=int(kwargs.get("training_months", 12)),
            val_ratio=float(kwargs.get("val_ratio", 0.2)),
        )
        if result is None or (isinstance(result, pd.DataFrame) and result.empty):
            raise ValueError(
                f"LightGBM produced no predictions for {target} "
                f"[{forecast_start} to {forecast_end}]. "
                f"Possible causes: insufficient training data, or all daily fits failed."
            )
        prediction_col = self._resolve_prediction_column(result, target)
        normalized = ensure_prediction_frame(result, prediction_col)
        output_root = ensure_runtime_dirs(Path(kwargs.get("output_root", "outputs/unified_runs")) / self.model_name / target)
        output_path = output_root / "predictions.csv"
        normalized.to_csv(output_path, index=False, encoding="utf-8-sig")
        return PredictionResult(model_name=self.model_name, target=target, output_path=output_path, frame=normalized)

    @staticmethod
    def _resolve_date_window(kwargs: dict) -> tuple[str, str]:
        start = kwargs.get("start")
        end = kwargs.get("end")
        if start and end:
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)
            return start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d")
        predict_date = kwargs.get("predict_date")
        if not predict_date:
            raise ValueError("lightGBM pipeline requires predict_date or start/end")
        predict_ts = pd.Timestamp(predict_date)
        return predict_ts.strftime("%Y-%m-%d"), predict_ts.strftime("%Y-%m-%d")

    @staticmethod
    def _resolve_prediction_column(frame, target: str) -> str:
        candidates = [
            "pred_y",
            "预测日前电价" if target == "dayahead" else "预测实时电价",
            "预测值",
        ]
        for col in candidates:
            if col in frame.columns:
                return col
        raise ValueError(f"Unsupported LightGBM prediction columns: {list(frame.columns)}")
