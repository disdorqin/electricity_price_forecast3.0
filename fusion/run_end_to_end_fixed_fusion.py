from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fusion.project_defaults import DEFAULTS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="End-to-end fixed-weight fusion: prepare model outputs for the prior 12 months, fit one set of weights, and export fused predictions."
    )
    parser.add_argument("--target-start", required=True, help="Inclusive prediction start date, YYYY-MM-DD.")
    parser.add_argument("--target-end", required=True, help="Inclusive prediction end date, YYYY-MM-DD.")
    parser.add_argument("--work-dir", required=True, help="Output directory for final fusion artifacts.")
    parser.add_argument("--history-days", type=int, default=365, help="Days of history used to fit fixed weights.")
    parser.add_argument("--chunk-days", type=int, default=31, help="Chunk size for slow model exporters.")
    parser.add_argument("--reg", type=float, default=0.1, help="Regularization for weight fitting.")
    parser.add_argument("--conda-env", default=None, help="Optional conda env for model wrapper commands.")
    parser.add_argument("--timemixer-device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--data-path-xlsx", default=str(DEFAULTS.data_xlsx))
    parser.add_argument("--data-path-csv", default=str(DEFAULTS.data_csv))
    parser.add_argument("--resume", action="store_true", help="Reuse existing chunk outputs when present.")
    parser.add_argument("--skip-timesfm", action="store_true")
    parser.add_argument("--skip-timemixer", action="store_true")
    parser.add_argument("--skip-sgdfnet", action="store_true")
    parser.add_argument("--skip-rt916", action="store_true")
    return parser


def _wrap_command(command: list[str], conda_env: str | None) -> list[str]:
    if not conda_env:
        return command
    return ["conda", "run", "-n", conda_env, *command]


def _run(command: list[str], conda_env: str | None) -> None:
    subprocess.run(_wrap_command(command, conda_env), check=True, cwd=str(PROJECT_ROOT))


def _train_window(target_start: str, history_days: int) -> tuple[str, str]:
    test_start = pd.Timestamp(target_start)
    train_end = test_start - pd.Timedelta(days=1)
    train_start = test_start - pd.Timedelta(days=history_days)
    return train_start.strftime("%Y-%m-%d"), train_end.strftime("%Y-%m-%d")


def main() -> None:
    args = build_parser().parse_args()
    train_start, train_end = _train_window(args.target_start, int(args.history_days))

    prepare_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "fusion" / "prepare_history_outputs.py"),
        "--start-date",
        train_start,
        "--end-date",
        args.target_end,
        "--chunk-days",
        str(args.chunk_days),
        "--data-path-xlsx",
        str(args.data_path_xlsx),
        "--data-path-csv",
        str(args.data_path_csv),
        "--timemixer-device",
        str(args.timemixer_device),
        "--skip-lightgbm",
    ]
    if args.resume:
        prepare_cmd.append("--resume")
    if args.skip_timesfm:
        prepare_cmd.append("--skip-timesfm")
    if args.skip_timemixer:
        prepare_cmd.append("--skip-timemixer")
    if args.skip_sgdfnet:
        prepare_cmd.append("--skip-sgdfnet")
    if args.skip_rt916:
        prepare_cmd.append("--skip-rt916")

    fusion_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "fusion" / "run_fixed_window_fusion.py"),
        "--train-start",
        train_start,
        "--train-end",
        train_end,
        "--test-start",
        args.target_start,
        "--test-end",
        args.target_end,
        "--work-dir",
        str(args.work_dir),
        "--reg",
        str(args.reg),
        "--data-path-xlsx",
        str(args.data_path_xlsx),
    ]

    print(f"[fusion] train window: {train_start} -> {train_end}")
    print(f"[fusion] test window: {args.target_start} -> {args.target_end}")
    _run(prepare_cmd, args.conda_env)
    _run(fusion_cmd, args.conda_env)


if __name__ == "__main__":
    main()
