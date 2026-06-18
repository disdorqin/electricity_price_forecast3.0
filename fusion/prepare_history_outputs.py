from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from fusion.project_defaults import DEFAULTS
else:
    from .project_defaults import DEFAULTS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare long-range model output history for rolling fusion backtests.")
    parser.add_argument("--start-date", required=True, help="Inclusive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Inclusive date, YYYY-MM-DD.")
    parser.add_argument("--chunk-days", type=int, default=31, help="Chunk size for slow per-day runners.")
    parser.add_argument("--data-path-xlsx", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--data-path-csv", default=str(DEFAULTS.data_csv))
    parser.add_argument("--skip-lightgbm", action="store_true")
    parser.add_argument("--skip-timesfm", action="store_true")
    parser.add_argument("--skip-timemixer", action="store_true")
    parser.add_argument("--skip-sgdfnet", action="store_true")
    parser.add_argument("--skip-rt916", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip chunk outputs that already exist.")
    parser.add_argument("--conda-env", default=None, help="Optional conda environment name to run model wrappers in.")
    parser.add_argument("--timemixer-device", default="auto", choices=["auto", "cpu", "cuda"])
    return parser


def _wrap_command(command: list[str], conda_env: str | None) -> list[str]:
    if not conda_env:
        return command
    return ["conda", "run", "-n", conda_env, *command]


def _run(command: list[str], conda_env: str | None, extra_env: dict[str, str] | None = None) -> None:
    env = None
    if extra_env:
        env = dict(os.environ)
        env.update(extra_env)
    subprocess.run(_wrap_command(command, conda_env), check=True, cwd=str(PROJECT_ROOT), env=env)


def _date_chunks(start_date: str, end_date: str, chunk_days: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    chunks: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + pd.Timedelta(days=chunk_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + pd.Timedelta(days=1)
    return chunks


def _concat_csvs(paths: list[Path], output: Path) -> None:
    frames = [pd.read_csv(path) for path in paths if path.exists()]
    if not frames:
        raise RuntimeError(f"No chunk CSVs found for {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(frames, ignore_index=True).to_csv(output, index=False, encoding="utf-8-sig")


def _filter_chunk_files(paths: list[Path], prefix: str) -> list[Path]:
    valid: list[Path] = []
    for path in paths:
        stem = path.stem
        if "probe" in stem:
            continue
        if not stem.startswith(prefix):
            continue
        parts = stem.split("_")
        if len(parts) < 3:
            continue
        if not (parts[-1].isdigit() and parts[-2].isdigit()):
            continue
        valid.append(path)
    return sorted(valid)


def _covers_range(path: Path, start_date: str, end_date: str, time_col: str) -> bool:
    if not path.exists():
        return False
    try:
        df = pd.read_csv(path)
    except Exception:
        return False
    if time_col not in df.columns or df.empty:
        return False
    ts = pd.to_datetime(df[time_col], errors="coerce").dropna()
    if ts.empty:
        return False
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    return ts.min() <= start and ts.max() >= end


def _prepare_lightgbm(args: argparse.Namespace, chunks: list[tuple[pd.Timestamp, pd.Timestamp]]) -> None:
    for task in ["dayahead", "realtime"]:
        chunk_paths: list[Path] = []
        for chunk_start, chunk_end in chunks:
            output = DEFAULTS.lightgbm_output / f"lightgbm_{task}_{chunk_start:%Y%m%d}_{chunk_end:%Y%m%d}.csv"
            if args.resume and output.exists():
                chunk_paths.append(output)
                continue
            command = [
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_lightgbm_export.py"),
                "--task",
                task,
                "--forecast-start",
                chunk_start.strftime("%Y-%m-%d"),
                "--forecast-end",
                chunk_end.strftime("%Y-%m-%d"),
                "--data-path",
                str(args.data_path_xlsx),
                "--output",
                str(output),
            ]
            if task == "realtime":
                command.append("--use-predicted-temp")
            _run(
                command,
                args.conda_env,
                extra_env={
                    "LGBM_DEVICE": os.getenv("LGBM_DEVICE", "cpu"),
                    "LGBM_N_JOBS": os.getenv("LGBM_N_JOBS", "4"),
                },
            )
            chunk_paths.append(output)
        _concat_csvs(chunk_paths, DEFAULTS.lightgbm_output / f"lightgbm_{task}.csv")


def _prepare_timesfm(args: argparse.Namespace, chunks: list[tuple[pd.Timestamp, pd.Timestamp]]) -> None:
    for task in ["dayahead", "realtime"]:
        chunk_paths: list[Path] = []
        for chunk_start, chunk_end in chunks:
            output = DEFAULTS.timesfm_output / f"backtest_{task}_{chunk_start:%Y%m%d}_{chunk_end:%Y%m%d}.csv"
            if args.resume and output.exists():
                chunk_paths.append(output)
                continue
            command = [
                sys.executable,
                str(PROJECT_ROOT / "fusion" / "runners" / "run_timesfm_export.py"),
                "--task",
                task,
                "--start-date",
                chunk_start.strftime("%Y-%m-%d"),
                "--end-date",
                chunk_end.strftime("%Y-%m-%d"),
                "--data-path",
                str(args.data_path_xlsx),
                "--output",
                str(output),
            ]
            _run(command, args.conda_env)
            chunk_paths.append(output)
        _concat_csvs(chunk_paths, DEFAULTS.timesfm_output / f"backtest_{task}.csv")


def _prepare_timemixer(args: argparse.Namespace) -> None:
    if args.resume:
        da_path = DEFAULTS.timemixer_output / "predictions_day_ahead_last_month.csv"
        rt_path = DEFAULTS.timemixer_output / "predictions_realtime_last_month.csv"
        if _covers_range(da_path, args.start_date, args.end_date, "ds") and _covers_range(rt_path, args.start_date, args.end_date, "ds"):
            return
    command = [
        sys.executable,
        str(PROJECT_ROOT / "fusion" / "runners" / "run_timemixer_export.py"),
        "--task",
        "realtime",
        "--test-start",
        args.start_date,
        "--test-end-exclusive",
        (pd.Timestamp(args.end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "--data-path",
        str(args.data_path_csv),
        "--output-dir",
        str(DEFAULTS.timemixer_output),
        "--device",
        str(args.timemixer_device),
    ]
    _run(command, args.conda_env)


def _prepare_sgdfnet(args: argparse.Namespace) -> None:
    chunk_paths: list[Path] = []
    for chunk_start, chunk_end in _date_chunks(args.start_date, args.end_date, int(args.chunk_days)):
        output = DEFAULTS.sgdfnet_output / f"predictions_{chunk_start:%Y%m%d}_{chunk_end:%Y%m%d}.csv"
        if args.resume and _covers_range(output, chunk_start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"), "timestamp"):
            chunk_paths.append(output)
            continue
        command = [
            sys.executable,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_sgdfnet_export.py"),
            "--forecast-start",
            chunk_start.strftime("%Y-%m-%d"),
            "--forecast-end",
            chunk_end.strftime("%Y-%m-%d"),
            "--data-path",
            str(args.data_path_xlsx),
            "--output",
            str(output),
        ]
        _run(command, args.conda_env)
        chunk_paths.append(output)
    _concat_csvs(chunk_paths, DEFAULTS.sgdfnet_output / "predictions.csv")


def _prepare_rt916(args: argparse.Namespace, chunks: list[tuple[pd.Timestamp, pd.Timestamp]]) -> None:
    chunk_paths: list[Path] = []
    for chunk_start, chunk_end in chunks:
        output = DEFAULTS.rt916_output / "dayahead" / f"rt916_{chunk_start:%Y%m%d}_{chunk_end:%Y%m%d}.csv"
        if args.resume and output.exists():
            chunk_paths.append(output)
            continue
        command = [
            sys.executable,
            str(PROJECT_ROOT / "fusion" / "runners" / "run_rt916_export.py"),
            "--task",
            "dayahead",
            "--start",
            f"{chunk_start:%Y-%m-%d} 01:00:00",
            "--end",
            f"{(chunk_end + pd.Timedelta(days=1)):%Y-%m-%d} 00:00:00",
            "--data-path",
            str(args.data_path_xlsx),
            "--output",
            str(output),
        ]
        _run(command, args.conda_env)
        chunk_paths.append(output)
    _concat_csvs(chunk_paths, DEFAULTS.rt916_output / "dayahead" / "rt916_dayahead.csv")


def main() -> None:
    args = build_parser().parse_args()
    chunks = _date_chunks(args.start_date, args.end_date, int(args.chunk_days))

    if not args.skip_lightgbm:
        _prepare_lightgbm(args, chunks)
    if not args.skip_timesfm:
        _prepare_timesfm(args, chunks)
    if not args.skip_timemixer:
        _prepare_timemixer(args)
    if not args.skip_sgdfnet:
        _prepare_sgdfnet(args)
    if not args.skip_rt916:
        _prepare_rt916(args, chunks)


if __name__ == "__main__":
    main()
