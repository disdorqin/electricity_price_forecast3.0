from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.project_defaults import DEFAULTS
from fusion.run_fixed_window_fusion import (
    ModelArtifact,
    _apply_fixed_weights,
    _build_artifacts,
    _final_result_table,
    _arbitrage_summary,
    _overwrite_truth_from_source,
    _period_summary,
    _realtime_join,
    _load_normalized_predictions,
)
from fusion.weights import fit_weights_from_long_table


@dataclass(frozen=True)
class WindowSpec:
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    train_months: int
    val_ratio: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full fusion pipeline: simulation month learns fixed weights, formal window applies them."
    )
    parser.add_argument("--target-start", required=True, help="Inclusive prediction start date, YYYY-MM-DD.")
    parser.add_argument("--target-end", required=True, help="Inclusive prediction end date, YYYY-MM-DD.")
    parser.add_argument("--work-dir", required=True, help="Output directory for all final artifacts.")
    parser.add_argument("--conda-env", default=None, help="Optional conda env name for model runners.")
    parser.add_argument("--data-path-xlsx", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--data-path-csv", default=str(DEFAULTS.data_csv))
    parser.add_argument("--timemixer-device", default="cuda", choices=["auto", "cpu", "cuda"])
    # The current merged day-ahead sweep found a segmented mix to be the best overlap setting.
    parser.add_argument("--reg", type=float, default=0.2)
    parser.add_argument("--reg-1-8", type=float, default=5.0)
    parser.add_argument("--reg-9-16", type=float, default=0.2)
    parser.add_argument("--reg-17-24", type=float, default=0.2)
    parser.add_argument("--simulation-train-months", type=int, default=6)
    parser.add_argument("--formal-train-months", type=int, default=3)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--sgdfnet-min-train-days", type=int, default=45)
    parser.add_argument("--rt916-retrain-daily", action="store_true")
    return parser


def _wrap_command(command: list[str], conda_env: str | None) -> list[str]:
    if not conda_env:
        return command
    return ["conda", "run", "-n", conda_env, *command]


def _run(command: list[str], conda_env: str | None, *, cwd: Path) -> None:
    subprocess.run(_wrap_command(command, conda_env), check=True, cwd=str(cwd))


def _days_inclusive(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return int((end.normalize() - start.normalize()).days) + 1


def _build_windows(target_start: str, target_end: str, simulation_train_months: int, formal_train_months: int, val_ratio: float) -> tuple[WindowSpec, WindowSpec]:
    target_start_ts = pd.Timestamp(target_start).normalize()
    target_end_ts = pd.Timestamp(target_end).normalize()
    simulation_start = (target_start_ts - pd.DateOffset(months=1)).normalize()
    simulation_end = target_start_ts - pd.Timedelta(days=1)
    simulation = WindowSpec(
        label="simulation",
        start=simulation_start,
        end=simulation_end,
        train_months=int(simulation_train_months),
        val_ratio=float(val_ratio),
    )
    formal = WindowSpec(
        label="formal",
        start=target_start_ts,
        end=target_end_ts,
        train_months=int(formal_train_months),
        val_ratio=float(val_ratio),
    )
    return simulation, formal


def _sgdfnet_split(window: WindowSpec, min_train_days: int) -> tuple[int, int]:
    total_days = max(5, _days_inclusive(window.start - pd.DateOffset(months=window.train_months), window.start - pd.Timedelta(days=1)))
    val_days = max(1, int(round(total_days * window.val_ratio)))
    train_lookback_days = max(int(min_train_days), total_days - val_days)
    return train_lookback_days, val_days


def _run_timesfm(window: WindowSpec, phase_dir: Path, args: argparse.Namespace) -> dict[str, Path]:
    outputs: dict[str, Path] = {}
    for task in ["dayahead", "realtime"]:
        output = phase_dir / "timesfm" / f"backtest_{task}.csv"
        command = [
            sys.executable,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_timesfm_export.py"),
            "--task",
            task,
            "--start-date",
            window.start.strftime("%Y-%m-%d"),
            "--end-date",
            window.end.strftime("%Y-%m-%d"),
            "--data-path",
            str(args.data_path_xlsx),
            "--output",
            str(output),
        ]
        _run(command, args.conda_env, cwd=PROJECT_ROOT)
        outputs[task] = output
    return outputs


def _run_timemixer(window: WindowSpec, phase_dir: Path, args: argparse.Namespace) -> dict[str, Path]:
    output_dir = phase_dir / "timemixer"
    for task in ["dayahead", "realtime"]:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_timemixer_export.py"),
            "--task",
            task,
            "--test-start",
            window.start.strftime("%Y-%m-%d"),
            "--test-end-exclusive",
            (window.end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            "--data-path",
            str(args.data_path_csv),
            "--output-dir",
            str(output_dir),
            "--device",
            str(args.timemixer_device),
            "--train-months",
            str(window.train_months),
            "--val-ratio",
            str(window.val_ratio),
        ]
        _run(command, args.conda_env, cwd=PROJECT_ROOT)
    return {
        "dayahead": output_dir / "predictions_day_ahead_last_month.csv",
        "realtime": output_dir / "predictions_realtime_last_month.csv",
    }


def _run_sgdfnet(window: WindowSpec, phase_dir: Path, args: argparse.Namespace) -> dict[str, Path]:
    output = phase_dir / "sgdfnet" / "predictions.csv"
    train_lookback_days, val_days = _sgdfnet_split(window, int(args.sgdfnet_min_train_days))
    command = [
        sys.executable,
        str(PROJECT_ROOT / "fusion" / "runners" / "run_sgdfnet_export.py"),
        "--forecast-start",
        window.start.strftime("%Y-%m-%d"),
        "--forecast-end",
        window.end.strftime("%Y-%m-%d"),
        "--data-path",
        str(args.data_path_xlsx),
        "--output",
        str(output),
        "--val-days",
        str(val_days),
        "--train-lookback-days",
        str(train_lookback_days),
        "--train-min-rows",
        str(max(24 * int(args.sgdfnet_min_train_days), 24)),
    ]
    _run(command, args.conda_env, cwd=PROJECT_ROOT)
    return {"realtime": output}


def _run_rt916(window: WindowSpec, phase_dir: Path, args: argparse.Namespace) -> dict[str, Path]:
    output = phase_dir / "rt916" / "dayahead" / "rt916_dayahead.csv"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "fusion" / "runners" / "run_rt916_export.py"),
        "--task",
        "dayahead",
        "--start",
        f"{window.start:%Y-%m-%d} 01:00:00",
        "--end",
        f"{(window.end + pd.Timedelta(days=1)):%Y-%m-%d} 00:00:00",
        "--data-path",
        str(args.data_path_xlsx),
        "--output",
        str(output),
        "--train-months",
        str(window.train_months),
        "--val-ratio",
        str(window.val_ratio),
    ]
    if args.rt916_retrain_daily:
        command.append("--retrain-daily")
    _run(command, args.conda_env, cwd=PROJECT_ROOT)
    return {"dayahead": output}


def _phase_artifacts(args: argparse.Namespace, phase_dir: Path) -> list[ModelArtifact]:
    merged_root = DEFAULTS.merged_dayahead_source_root
    merged_timesfm_da = merged_root / "timesfm_dayahead.csv"
    merged_timemixer_da = merged_root / "timemixer_dayahead.csv"
    merged_rt916_da = merged_root / "rt916_dayahead.csv"

    return [
        ModelArtifact(
            model_name="TimesFM",
            adapter="timesfm",
            source=merged_timesfm_da if merged_timesfm_da.exists() else phase_dir / "timesfm" / "backtest_dayahead.csv",
            task="dayahead",
            adapter_kwargs={"task": "dayahead", "data_path": str(args.data_path_xlsx)},
        ),
        ModelArtifact(
            model_name="TimesFM",
            adapter="timesfm",
            source=phase_dir / "timesfm" / "backtest_realtime.csv",
            task="realtime",
            adapter_kwargs={"task": "realtime", "data_path": str(args.data_path_xlsx)},
        ),
        ModelArtifact(
            model_name="TimeMixer",
            adapter="timemixer",
            source=merged_timemixer_da if merged_timemixer_da.exists() else phase_dir / "timemixer" / "predictions_day_ahead_last_month.csv",
            task="dayahead",
            adapter_kwargs={"task": "dayahead"},
        ),
        ModelArtifact(
            model_name="TimeMixer",
            adapter="timemixer",
            source=phase_dir / "timemixer" / "predictions_realtime_last_month.csv",
            task="realtime",
            adapter_kwargs={"task": "realtime"},
        ),
        ModelArtifact(
            model_name="SGDFNet",
            adapter="sgdfnet",
            source=phase_dir / "sgdfnet" / "predictions.csv",
            task="realtime",
            adapter_kwargs={},
        ),
        ModelArtifact(
            model_name="RT916_SpikeFusionNet",
            adapter="rt916",
            source=merged_rt916_da if merged_rt916_da.exists() else phase_dir / "rt916" / "dayahead" / "rt916_dayahead.csv",
            task="dayahead",
            adapter_kwargs={"task": "dayahead"},
        ),
    ]


def _collect_phase_predictions(args: argparse.Namespace, phase_dir: Path, label: str) -> pd.DataFrame:
    artifacts = _phase_artifacts(args, phase_dir)
    normalized = _load_normalized_predictions(artifacts)
    normalized = _overwrite_truth_from_source(normalized, str(args.data_path_xlsx))
    normalized.to_csv(phase_dir / f"{label}_predictions_long.csv", index=False, encoding="utf-8-sig")
    return normalized


def _run_phase(window: WindowSpec, args: argparse.Namespace, root_dir: Path) -> pd.DataFrame:
    phase_dir = root_dir / window.label
    (phase_dir / "timesfm").mkdir(parents=True, exist_ok=True)
    (phase_dir / "timemixer").mkdir(parents=True, exist_ok=True)
    (phase_dir / "sgdfnet").mkdir(parents=True, exist_ok=True)
    (phase_dir / "rt916" / "dayahead").mkdir(parents=True, exist_ok=True)

    _run_timesfm(window, phase_dir, args)
    _run_timemixer(window, phase_dir, args)
    _run_sgdfnet(window, phase_dir, args)
    _run_rt916(window, phase_dir, args)
    return _collect_phase_predictions(args, phase_dir, window.label)


def _fit_weights(simulation_df: pd.DataFrame, reg: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    weights_df, fit_report = fit_weights_from_long_table(simulation_df, reg=reg)
    if weights_df.empty:
        raise RuntimeError("No weights were fit from simulation predictions.")
    return weights_df, fit_report


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    simulation_window, formal_window = _build_windows(
        args.target_start,
        args.target_end,
        int(args.simulation_train_months),
        int(args.formal_train_months),
        float(args.val_ratio),
    )

    simulation_df = _run_phase(simulation_window, args, work_dir)
    formal_df = _run_phase(formal_window, args, work_dir)

    reg_map = {
        period: value
        for period, value in {
            "1_8": args.reg_1_8,
            "9_16": args.reg_9_16,
            "17_24": args.reg_17_24,
        }.items()
        if value is not None
    }
    weights_df, fit_report = fit_weights_from_long_table(simulation_df, reg=float(args.reg), reg_map=reg_map or None)
    weights_df.to_csv(work_dir / "fixed_weights.csv", index=False, encoding="utf-8-sig")
    fit_report.to_csv(work_dir / "weight_fit_report.csv", index=False, encoding="utf-8-sig")

    da_fused = _apply_fixed_weights(
        formal_df,
        weights_df,
        task="dayahead",
        test_start=formal_window.start.strftime("%Y-%m-%d"),
        test_end=formal_window.end.strftime("%Y-%m-%d"),
    )
    rt_fused = _apply_fixed_weights(
        formal_df,
        weights_df,
        task="realtime",
        test_start=formal_window.start.strftime("%Y-%m-%d"),
        test_end=formal_window.end.strftime("%Y-%m-%d"),
    )

    da_dir = work_dir / "dayahead"
    rt_dir = work_dir / "realtime"
    da_dir.mkdir(parents=True, exist_ok=True)
    rt_dir.mkdir(parents=True, exist_ok=True)
    da_fused.to_csv(da_dir / "fused_predictions.csv", index=False, encoding="utf-8-sig")
    rt_fused.to_csv(rt_dir / "fused_predictions.csv", index=False, encoding="utf-8-sig")

    da_metrics = _period_summary(da_fused, task="dayahead", y_true_col="y_true", y_pred_col="y_fused")
    rt_metrics = _period_summary(rt_fused, task="realtime", y_true_col="y_true", y_pred_col="y_fused")
    joined_rt = _realtime_join(da_fused, rt_fused)
    rt_arbitrage = _arbitrage_summary(joined_rt)

    final_table = _final_result_table(da_fused, rt_fused)
    final_table.to_csv(work_dir / "final_truth_vs_fusion.csv", index=False, encoding="utf-8-sig")
    da_metrics.to_csv(da_dir / "metrics_smape.csv", index=False, encoding="utf-8-sig")
    rt_metrics.to_csv(rt_dir / "metrics_smape.csv", index=False, encoding="utf-8-sig")
    joined_rt.to_csv(rt_dir / "joined_for_arbitrage.csv", index=False, encoding="utf-8-sig")
    rt_arbitrage.to_csv(rt_dir / "metrics_arbitrage.csv", index=False, encoding="utf-8-sig")

    metrics_summary = pd.concat([da_metrics, rt_metrics, rt_arbitrage], ignore_index=True, sort=False)
    metrics_summary.to_csv(work_dir / "metrics_summary.csv", index=False, encoding="utf-8-sig")

    runtime_summary = {
        "target_start": formal_window.start.strftime("%Y-%m-%d"),
        "target_end": formal_window.end.strftime("%Y-%m-%d"),
        "simulation_start": simulation_window.start.strftime("%Y-%m-%d"),
        "simulation_end": simulation_window.end.strftime("%Y-%m-%d"),
        "simulation_train_months": int(simulation_window.train_months),
        "formal_train_months": int(formal_window.train_months),
        "val_ratio": float(args.val_ratio),
        "runtime_seconds": round(time.time() - started, 2),
        "final_rows": int(len(final_table)),
    }
    (work_dir / "runtime_summary.json").write_text(
        json.dumps(runtime_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
