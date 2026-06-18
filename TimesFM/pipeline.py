from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from fusion.project_defaults import DEFAULTS
from pipelines.base import BaseModelPipeline, PredictionResult
from utils.io import ensure_prediction_frame, ensure_runtime_dirs

from .infer import PREDICTION_COL, TIMESTAMP_COL, predict_price_for_range

logger = logging.getLogger(__name__)


class ModelPipeline(BaseModelPipeline):
    model_name = "timesfm"
    device_type = "gpu"

    def train(self, **kwargs):
        raise NotImplementedError("TimesFM current integration keeps the original project layout and does not expose train().")

    def predict(self, **kwargs):
        return self.predict_range(**kwargs)

    def predict_range(self, target: str, **kwargs):
        data_path = kwargs.get("data_path")
        if not data_path:
            raise ValueError("TimesFM pipeline requires data_path")

        start, end = self._resolve_date_window(kwargs)
        cached = self._load_cached_prediction(target=target, start=start, end=end)
        if cached is not None:
            normalized = ensure_prediction_frame(cached, PREDICTION_COL)
            output_root = ensure_runtime_dirs(Path(kwargs.get("output_root", "outputs/unified_runs")) / self.model_name / target)
            output_path = output_root / "predictions.csv"
            normalized.to_csv(output_path, index=False, encoding="utf-8-sig")
            return PredictionResult(model_name=self.model_name, target=target, output_path=output_path, frame=normalized)

        ready, reason = self._local_model_ready_status()
        if not ready:
            logger.warning("Skipping TimesFM for %s/%s: %s", target, start, reason)
            return None

        result = predict_price_for_range(
            data_path=data_path,
            start_date=start,
            end_date=end,
            target=target,
            segment_count=int(kwargs.get("segment_count", 3)),
            seed=int(kwargs.get("seed", 42)),
            deterministic=bool(kwargs.get("deterministic", True)),
            verbose=bool(kwargs.get("verbose", True)),
        )
        normalized = ensure_prediction_frame(result, PREDICTION_COL)
        output_root = ensure_runtime_dirs(Path(kwargs.get("output_root", "outputs/unified_runs")) / self.model_name / target)
        output_path = output_root / "predictions.csv"
        normalized.to_csv(output_path, index=False, encoding="utf-8-sig")
        return PredictionResult(model_name=self.model_name, target=target, output_path=output_path, frame=normalized)

    @staticmethod
    def _resolve_date_window(kwargs: dict) -> tuple[str, str]:
        start = kwargs.get("start")
        end = kwargs.get("end")
        if start and end:
            return str(start), str(end)
        predict_date = kwargs.get("predict_date")
        if not predict_date:
            raise ValueError("TimesFM pipeline requires predict_date or start/end")
        return str(predict_date), str(predict_date)

    @staticmethod
    def _candidate_cache_files(target: str) -> Iterable[Path]:
        task_name = "dayahead" if target == "dayahead" else "realtime"
        yield DEFAULTS.timesfm_output / f"backtest_{task_name}.csv"
        yield from sorted(DEFAULTS.timesfm_output.glob(f"backtest_{task_name}_*.csv"))
        yield from sorted(Path("fusion_runs").glob(f"**/timesfm/backtest_{task_name}.csv"))

    @classmethod
    def _load_cached_prediction(cls, *, target: str, start: str, end: str) -> pd.DataFrame | None:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)
        for path in cls._candidate_cache_files(target):
            if not path.exists():
                continue
            try:
                df = pd.read_csv(path)
            except Exception:
                continue
            df = df.copy()
            if TIMESTAMP_COL not in df.columns and "閺冭泛鍩?" in df.columns:
                df = df.rename(columns={"閺冭泛鍩?": TIMESTAMP_COL})
            if TIMESTAMP_COL not in df.columns:
                continue
            if PREDICTION_COL not in df.columns:
                pred_candidates = [col for col in df.columns if col != TIMESTAMP_COL]
                if not pred_candidates:
                    continue
                df = df.rename(columns={pred_candidates[0]: PREDICTION_COL})
            df[TIMESTAMP_COL] = pd.to_datetime(df[TIMESTAMP_COL], errors="coerce")
            df = df.dropna(subset=[TIMESTAMP_COL])
            window = df[(df[TIMESTAMP_COL] >= start_ts) & (df[TIMESTAMP_COL] < end_ts)].copy()
            if len(window) >= 24:
                return window
        return None

    @staticmethod
    def _local_model_ready_status() -> tuple[bool, str]:
        model_dir = Path(DEFAULTS.timesfm_model_dir)
        if not model_dir.exists():
            return False, f"local model directory does not exist: {model_dir}"
        materialized_files = [p for p in model_dir.rglob("*") if p.is_file() and p.name != ".gitignore"]
        has_real_model_files = any(
            p.suffix.lower() in {".bin", ".ckpt", ".json", ".safetensors"} and "huggingface\\download" not in str(p).lower()
            for p in materialized_files
        )
        if not has_real_model_files:
            return False, f"model weights are missing: {model_dir}"
        return True, ""
