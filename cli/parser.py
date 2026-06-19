from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified electricity forecast entrypoint")
    parser.add_argument(
        "--pipeline",
        required=True,
        choices=[
            "predict",
            "train",
            "evaluate",
            "fusion",
            "sync_dataset",
            "model_stage",
            "learner_stage",
            "fuse_stage",
            "classifier_stage",
        ],
    )
    parser.add_argument("--target", default="both", choices=["dayahead", "realtime", "both"])
    parser.add_argument("--models", default="all", help="Comma-separated model names or all")
    parser.add_argument("--stage-models", default="formal", help="Staged execution model set: formal, all, or comma-separated names")
    parser.add_argument("--date", default=None, help="Single target day, YYYY-MM-DD")
    parser.add_argument("--start", default=None, help="Range start, YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Range end, YYYY-MM-DD")
    parser.add_argument("--data-path", default="data/shandong_pmos_hourly.xlsx")
    parser.add_argument("--output-root", default="outputs/unified_runs")
    parser.add_argument("--max-cpu-workers", type=int, default=2)
    parser.add_argument("--max-gpu-workers", type=int, default=1)
    parser.add_argument("--training-months", type=int, default=12)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--use-predicted-temp", action="store_true")
    parser.add_argument("--segment-count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--pred-path", default=None)
    parser.add_argument("--actual-path", default=None)
    parser.add_argument("--fusion-work-dir", default="fusion_runs/unified_entry")
    parser.add_argument("--train-length-decision", default="fusion_runs/repro_training_length_probe/repro_training_length_decision.json")
    parser.add_argument("--weight-lower-bound", type=float, default=-0.5)
    parser.add_argument("--weight-upper-bound", type=float, default=1.2)
    parser.add_argument("--conda-env", default="")
    parser.add_argument("--use-classifier", action="store_true", default=False)
    parser.add_argument("--clf-data", default=None)
    parser.add_argument("--daily-run-root", default="daily_runs")
    parser.add_argument("--validation-days", type=int, default=30, help="Number of days for the validation window (default: 30). Used by model_stage for weight fitting.")
    return parser
