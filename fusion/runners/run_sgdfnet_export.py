from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SGDFNET_SRC = PROJECT_ROOT / "SGDFNet" / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SGDFNET_SRC) not in sys.path:
    sys.path.insert(0, str(SGDFNET_SRC))

from fusion.project_defaults import DEFAULTS
from sgdfnet.protocol_b_cutoff import run_protocol_b_cutoff_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SGDFNet cutoff-safe realtime export for fusion.")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "SGDFNet" / "configs" / "cutoff_recovery_2026_diag_a_prune_actualside.yaml"),
        help="Base SGDFNet cutoff config to clone and override at runtime.",
    )
    parser.add_argument("--forecast-start", required=True, help="Inclusive target day start, YYYY-MM-DD.")
    parser.add_argument("--forecast-end", required=True, help="Inclusive target day end, YYYY-MM-DD.")
    parser.add_argument("--data-path", default=str(DEFAULTS.data_xlsx), help="Excel dataset path.")
    parser.add_argument("--output", default=None, help="Stable CSV path for exported predictions.")
    parser.add_argument(
        "--run-root",
        default=str(DEFAULTS.sgdfnet_output / "runs"),
        help="Directory where timestamped SGDFNet run folders are stored.",
    )
    parser.add_argument("--val-days", type=int, default=None, help="Optional override for validation days.")
    parser.add_argument("--train-min-rows", type=int, default=None, help="Optional override for minimum train rows.")
    parser.add_argument("--train-lookback-days", type=int, default=None, help="Optional rolling lookback window for training rows.")
    return parser


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.exists():
        shutil.copy2(source, target)


def main() -> None:
    args = build_parser().parse_args()
    output = Path(args.output) if args.output else DEFAULTS.sgdfnet_output / "predictions.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    run_root = Path(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.config)
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    config["data_path"] = str(Path(args.data_path))
    config["output_root"] = str(run_root)
    config["start_day"] = str(args.forecast_start)
    config["end_day"] = str(args.forecast_end)
    if args.val_days is not None:
        config["val_days"] = int(args.val_days)
    if args.train_min_rows is not None:
        config["train_min_rows"] = int(args.train_min_rows)
    if args.train_lookback_days is not None:
        config["train_lookback_days"] = int(args.train_lookback_days)

    runtime_config = output.parent / "sgdfnet_runtime_config.yaml"
    with runtime_config.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)

    run_dir = run_protocol_b_cutoff_experiment(runtime_config)
    predictions_path = Path(run_dir) / "predictions.csv"
    if not predictions_path.exists():
        raise FileNotFoundError(f"SGDFNet predictions.csv not found under {run_dir}")

    shutil.copy2(predictions_path, output)
    _copy_if_exists(Path(run_dir) / "monthly_summary.csv", output.parent / "monthly_summary.csv")
    _copy_if_exists(Path(run_dir) / "metrics_summary.json", output.parent / "metrics_summary.json")
    _copy_if_exists(Path(run_dir) / "segment_metrics.csv", output.parent / "segment_metrics.csv")
    _copy_if_exists(Path(run_dir) / "tail_metrics.csv", output.parent / "tail_metrics.csv")

    export_meta = {
        "run_dir": str(run_dir),
        "runtime_config": str(runtime_config),
        "predictions_csv": str(output),
    }
    (output.parent / "export_meta.json").write_text(
        json.dumps(export_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
