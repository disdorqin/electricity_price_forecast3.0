from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from pipelines.base import BaseModelPipeline, PredictionResult
from utils.io import ensure_prediction_frame, ensure_runtime_dirs

from .repro_pipeline import RunConfig, run_monthly_reproduction


DEFAULT_TIMEMIXER_CSV = r"D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\epf\data\shandong_pmos_hourly.csv"
DEFAULT_DATA_XLSX = r"D:\作业\大创_挑战杯_互联网\大学生创新创业计划\大创实现\其他资料\electricity_forecast_model2.0\data\shandong_pmos_hourly.xlsx"


class ModelPipeline(BaseModelPipeline):
    model_name = "timemixer"
    device_type = "gpu"

    def train(self, **kwargs):
        raise NotImplementedError("TimeMixer unified train() is not wired yet; use predict_range or legacy pipeline.")

    def predict(self, **kwargs) -> PredictionResult:
        return self.predict_range(**kwargs)

    def predict_range(self, target: str, **kwargs) -> PredictionResult:
        output_root = ensure_runtime_dirs(Path(kwargs.get("output_root", "outputs/unified_runs")) / self.model_name / target)
        predict_date = pd.Timestamp(kwargs.get("predict_date"))
        month = predict_date.strftime("%Y-%m")

        start_date = kwargs.get("start") or predict_date.strftime("%Y-%m-%d")
        end_date = kwargs.get("end") or predict_date.strftime("%Y-%m-%d")
        # TimeMixer 的 run_monthly_reproduction 使用半开区间 [test_start, test_end_exclusive)。
        # 当调用方传入 start == end 时（单日预测），应将 end 视为包含该日，
        # 因此 test_end_exclusive = end + 1 天，否则 test_days 为空。
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        if end_ts <= start_ts:
            end_exclusive = (end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            end_exclusive = end_date

        run_cfg = RunConfig(
            data_path=self._prepare_data_path(kwargs.get("data_path")),
            output_dir=str(output_root),
            month=month,
            test_start=start_date,
            test_end_exclusive=end_exclusive,
            append_leaderboard=False,
            train_months=int(kwargs.get("training_months", 12)),
            val_ratio=float(kwargs.get("val_ratio", 0.2)),
        )
        result = run_monthly_reproduction(run_cfg)
        raw = pd.read_csv(Path(result["output_dir"]) / "predictions_raw.csv", encoding="utf-8-sig")
        if raw.empty:
            raise ValueError(f"TimeMixer produced empty predictions_raw for {predict_date.date()}")
        # predictions_raw uses 'y_pred' for both DA and RT tasks
        # 'pred_day_ahead_price' is only populated in RT rows (as DA injection reference)
        prediction_col = "y_pred"
        task_filter = "da" if target == "dayahead" else "rt"
        if "task" in raw.columns:
            filtered = raw[raw["task"] == task_filter].copy()
        else:
            filtered = raw.copy()
        if filtered.empty:
            raise ValueError(f"TimeMixer task={task_filter} filter yielded 0 rows for {predict_date.date()}")
        normalized = ensure_prediction_frame(filtered.rename(columns={"ds": "时刻"}), prediction_col)
        output_path = output_root / "predictions.csv"
        normalized.to_csv(output_path, index=False, encoding="utf-8-sig")
        return PredictionResult(model_name=self.model_name, target=target, output_path=output_path, frame=normalized)

    @staticmethod
    def _prepare_data_path(data_path: str | None) -> str:
        if not data_path:
            # Prefer the xlsx in the project data/ folder
            if Path(DEFAULT_DATA_XLSX).exists():
                data_path = DEFAULT_DATA_XLSX
            elif Path(DEFAULT_TIMEMIXER_CSV).exists():
                return DEFAULT_TIMEMIXER_CSV
            else:
                return DEFAULT_DATA_XLSX  # let it error clearly if missing
        path = Path(data_path)
        if path.suffix.lower() != ".xlsx":
            return str(path)
        # Convert xlsx -> csv with explicit engine and encoding
        df = pd.read_excel(path, engine="openpyxl")
        tmp_dir = Path(tempfile.gettempdir()) / "timemixer_unified_cache"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        csv_path = tmp_dir / f"{path.stem}.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        return str(csv_path)
