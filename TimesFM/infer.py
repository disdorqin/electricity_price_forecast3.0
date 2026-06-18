from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


TIMESTAMP_COL = "timestamp"
PREDICTION_COL = "prediction"


def _load_legacy_module():
    project_root = Path(__file__).resolve().parents[1]
    candidates = [
        project_root / "TimesFM" / "_archive" / "price_forecast_copy_分时段预测.py",
        project_root / "TF" / "price_forecast_copy_分时段预测.py",
        project_root / "_archive" / "root_cleanup_20260617" / "TF" / "price_forecast_copy_分时段预测.py",
    ]
    source_path = next((path for path in candidates if path.exists()), None)
    if source_path is None:
        raise ModuleNotFoundError("TimesFM legacy forecasting script not found in TF/ or archived TF/.")

    spec = importlib.util.spec_from_file_location("timesfm_legacy_forecast", source_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load Timesfm legacy module from: {source_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LEGACY_MODULE = _load_legacy_module()
forecast_next_day = _LEGACY_MODULE.forecast_next_day
set_reproducibility = _LEGACY_MODULE.set_reproducibility


def _normalize_legacy_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if TIMESTAMP_COL not in out.columns:
        out = out.rename(columns={out.columns[0]: TIMESTAMP_COL})
    if PREDICTION_COL not in out.columns:
        pred_candidates = [col for col in out.columns if col != TIMESTAMP_COL]
        if not pred_candidates:
            raise ValueError(f"TimesFM legacy output has no prediction column: {list(out.columns)}")
        out = out.rename(columns={pred_candidates[0]: PREDICTION_COL})
    return out[[TIMESTAMP_COL, PREDICTION_COL]].copy()


def predict_price_for_date(
    data_path: str,
    forecast_date: str,
    *,
    target: str = "realtime",
    sheet: int | str | None = 0,
    encoding: str | None = None,
    segment_count: int = 3,
    seed: int = 42,
    deterministic: bool = True,
) -> pd.DataFrame:
    args = argparse.Namespace(
        mode="forecast",
        data=data_path,
        forecast_date=forecast_date,
        target=target,
        sheet=sheet,
        encoding=encoding,
        segment_count=segment_count,
        horizon=24,
        eval_days=30,
        exog_mode="pred",
        skip_style="normal",
        seed=seed,
        deterministic=deterministic,
        dump_csv=False,
    )
    set_reproducibility(int(seed), bool(deterministic))
    raw = forecast_next_day(args)
    return _normalize_legacy_frame(raw)


def _build_date_list(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((end - start).days + 1)]


def predict_price_for_range(
    data_path: str,
    start_date: str,
    end_date: str,
    *,
    target: str = "realtime",
    sheet: int | str | None = 0,
    encoding: str | None = None,
    segment_count: int = 3,
    seed: int = 42,
    deterministic: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    results: list[pd.DataFrame] = []
    for date_str in _build_date_list(start_date, end_date):
        if verbose:
            print(f"Predicting date: {date_str}")
        results.append(
            predict_price_for_date(
                data_path=data_path,
                forecast_date=date_str,
                target=target,
                sheet=sheet,
                encoding=encoding,
                segment_count=segment_count,
                seed=seed,
                deterministic=deterministic,
            )
        )
    if not results:
        return pd.DataFrame(columns=[TIMESTAMP_COL, PREDICTION_COL])
    return pd.concat(results, ignore_index=True)[[TIMESTAMP_COL, PREDICTION_COL]]
