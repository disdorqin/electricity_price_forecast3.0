from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TIMESFM_SRC = PROJECT_ROOT / "TimesFM" / "src"
if TIMESFM_SRC.exists() and str(TIMESFM_SRC) not in sys.path:
    sys.path.insert(0, str(TIMESFM_SRC))

from TimesFM.infer import predict_price_for_range
from fusion.project_defaults import DEFAULTS

logger = logging.getLogger(__name__)

TS_COL = "时刻"
PRED_COL = "预测值"
TRUTH_COL = "真实值"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export TimesFM predictions for fusion.")
    parser.add_argument("--task", required=True, choices=["dayahead", "realtime"])
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-path", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--output", required=True, help="CSV path for exported TimesFM output.")
    parser.add_argument("--segment-count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true")
    return parser


def _candidate_cache_files(task: str) -> Iterable[Path]:
    yield DEFAULTS.timesfm_output / f"backtest_{task}.csv"
    yield from sorted(DEFAULTS.timesfm_output.glob(f"backtest_{task}_*.csv"))
    yield from sorted((PROJECT_ROOT / "fusion_runs").glob(f"**/timesfm/backtest_{task}.csv"))


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    rename_map: dict[str, str] = {}
    for column in frame.columns:
        name = str(column)
        lower = name.lower()
        if name == TS_COL:
            continue
        if name == PRED_COL:
            continue
        if name == TRUTH_COL:
            continue
        if "时刻" in name or "timestamp" in lower or lower == "date":
            rename_map[column] = TS_COL
        elif "预测" in name or "prediction" in lower or lower.startswith("pred"):
            rename_map[column] = PRED_COL
        elif "真实" in name or "actual" in lower or "truth" in lower:
            rename_map[column] = TRUTH_COL
    if rename_map:
        frame = frame.rename(columns=rename_map)
    return frame


def _model_dir_ready() -> tuple[bool, str]:
    model_dir_env = (os.environ.get("TIMESFM_MODEL_DIR") or "").strip()
    model_dir = Path(model_dir_env) if model_dir_env else Path(DEFAULTS.timesfm_model_dir)
    if not model_dir.exists():
        return False, f"model directory missing: {model_dir}"
    required = [model_dir / "config.json", model_dir / "model.safetensors"]
    missing = [path.name for path in required if not path.exists()]
    if missing:
        return False, f"missing TimesFM model artifacts {missing} in {model_dir}"
    return True, ""


def _try_cached_export(*, task: str, start_date: str, end_date: str, output: Path) -> bool:
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    for path in _candidate_cache_files(task):
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        df = _normalize_columns(df)
        if TS_COL not in df.columns or PRED_COL not in df.columns:
            continue
        df[TS_COL] = pd.to_datetime(df[TS_COL], errors="coerce")
        df = df.dropna(subset=[TS_COL])
        window = df[(df[TS_COL] >= start_ts) & (df[TS_COL] < end_ts)].copy()
        if len(window) >= 24:
            output.parent.mkdir(parents=True, exist_ok=True)
            window.to_csv(output, index=False, encoding="utf-8-sig")
            logger.info("TimesFM export reused cached file: %s", path)
            return True
    return False


def _run_live_export(
    *,
    task: str,
    start_date: str,
    end_date: str,
    data_path: str,
    output: Path,
    segment_count: int,
    seed: int,
    deterministic: bool,
) -> None:
    ready, reason = _model_dir_ready()
    if not ready:
        raise FileNotFoundError(reason)
    result = predict_price_for_range(
        data_path=data_path,
        start_date=start_date,
        end_date=end_date,
        target=task,
        segment_count=segment_count,
        seed=seed,
        deterministic=deterministic,
        verbose=True,
    )
    if result is None or result.empty:
        raise RuntimeError("TimesFM live inference produced no rows.")
    frame = result.rename(columns={"timestamp": TS_COL, "prediction": PRED_COL}).copy()
    frame[TS_COL] = pd.to_datetime(frame[TS_COL], errors="coerce")
    frame = frame.dropna(subset=[TS_COL])
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, encoding="utf-8-sig")
    logger.info("TimesFM export completed via live inference.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = (PROJECT_ROOT / output).resolve()
    data_path = Path(args.data_path)
    if not data_path.is_absolute():
        data_path = (PROJECT_ROOT / data_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if _try_cached_export(
        task=args.task,
        start_date=args.start_date,
        end_date=args.end_date,
        output=output,
    ):
        return

    try:
        _run_live_export(
            task=args.task,
            start_date=args.start_date,
            end_date=args.end_date,
            data_path=str(data_path),
            output=output,
            segment_count=int(args.segment_count),
            seed=int(args.seed),
            deterministic=bool(args.deterministic),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"TimesFM export failed without cached fallback: {exc}") from exc


if __name__ == "__main__":
    main()
