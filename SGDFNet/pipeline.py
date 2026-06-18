from __future__ import annotations

import tempfile
from pathlib import Path
import sys

import pandas as pd
import yaml

from pipelines.base import BaseModelPipeline, PredictionResult
from utils.io import ensure_prediction_frame, ensure_runtime_dirs


SRC_ROOT = Path(__file__).resolve().parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sgdfnet.protocol_b_cutoff import run_protocol_b_cutoff_experiment  # noqa: E402


DEFAULT_CONFIG = Path(__file__).resolve().parent / "configs" / "cutoff_recovery_2026_diag_a_prune_actualside.yaml"


class ModelPipeline(BaseModelPipeline):
    model_name = "sgdfnet"
    device_type = "cpu"

    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path or DEFAULT_CONFIG)

    def train(self, **kwargs):
        return run_protocol_b_cutoff_experiment(self.config_path)

    def predict(self, **kwargs) -> PredictionResult:
        return self.predict_range(**kwargs)

    def predict_range(self, target: str, **kwargs) -> PredictionResult:
        output_root = ensure_runtime_dirs(
            Path(kwargs.get("output_root", "outputs/unified_runs")) / self.model_name / target
        )

        # Build a temporary config YAML with overrides from kwargs
        predict_date = pd.Timestamp(kwargs.get("predict_date", "2026-05-15"))
        data_path = kwargs.get("data_path")
        start = kwargs.get("start") or predict_date.strftime("%Y-%m-%d")
        end = kwargs.get("end") or predict_date.strftime("%Y-%m-%d")
        # SGDFNet core uses decision_days = [start_day-1 .. end_day-1],
        # so predictions cover [start_day .. end_day] inclusive.
        end_day = end

        tmp_config = self._build_temp_config(
            data_path=data_path,
            start_day=start,
            end_day=end_day,
            output_root=str(output_root / "sgdfnet_runs"),
        )

        run_dir = Path(run_protocol_b_cutoff_experiment(tmp_config))
        predictions = pd.read_csv(run_dir / "predictions.csv", encoding="utf-8-sig")

        # Detect timestamp column
        if "timestamp" in predictions.columns:
            ts_col = "timestamp"
        elif "ds" in predictions.columns:
            ts_col = "ds"
        else:
            ts_col = predictions.columns[0]

        predictions[ts_col] = pd.to_datetime(predictions[ts_col], errors="coerce")
        start_date = pd.Timestamp(start).normalize().date()
        end_date = pd.Timestamp(end).normalize().date()
        pred_dates = predictions[ts_col].dt.date
        mask = (pred_dates >= start_date) & (pred_dates <= end_date)
        filtered = predictions[mask].copy()

        if filtered.empty:
            available_min = pred_dates.min()
            available_max = pred_dates.max()
            raise ValueError(
                f"SGDFNet produced no predictions for [{start} .. {end}]. "
                f"Core returned {len(predictions)} rows covering "
                f"[{available_min} .. {available_max}]. "
                f"Possible causes: insufficient training data (train_min_rows=2160), "
                f"data gaps, or all decision_days skipped."
            )

        # Rename timestamp column to '时刻' for ensure_prediction_frame
        if ts_col != "时刻":
            filtered = filtered.rename(columns={ts_col: "时刻"})

        normalized = ensure_prediction_frame(filtered, "rt_hat")
        output_path = output_root / "predictions.csv"
        normalized.to_csv(output_path, index=False, encoding="utf-8-sig")
        return PredictionResult(
            model_name=self.model_name, target=target, output_path=output_path, frame=normalized
        )

    def _build_temp_config(
        self,
        data_path: str | None,
        start_day: str,
        end_day: str,
        output_root: str,
    ) -> Path:
        """Create a temporary YAML config overriding key fields from the base config."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            base_cfg = yaml.safe_load(f)

        # Override paths and date range
        if data_path:
            base_cfg["data_path"] = str(data_path)
        base_cfg["start_day"] = start_day
        base_cfg["end_day"] = end_day
        base_cfg["output_root"] = output_root

        # Write to temp file
        tmp_dir = Path(tempfile.gettempdir()) / "sgdfnet_staged_configs"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"staged_{start_day}_{end_day}.yaml"
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(base_cfg, f, allow_unicode=True, default_flow_style=False)

        return tmp_path
