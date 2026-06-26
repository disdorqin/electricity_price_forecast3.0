from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified electricity forecast entrypoint")
    parser.add_argument(
        "pos_date", nargs="?", default=None,
        help="Target date (YYYY-MM-DD). Shortcut for --pipeline full --date <DATE>",
    )
    parser.add_argument(
        "--pipeline",
        default="full",
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
            "full",
            # New ledger production pipeline
            "ledger_predict",
            "ledger_backfill",
            "ledger_weight",
            "ledger_fuse",
            "ledger_classifier",
            "ledger_full",
            "ledger_smoke",
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
    # Ledger pipeline parameters
    parser.add_argument("--epf-v1-root", default=None, help="[REQUIRED for ledger] Path to EPF v1.0 repository root for LightGBM/TimesFM adapters")
    parser.add_argument("--epf-v1-mode", default="exact", choices=["exact", "cutoff_safe"], help="EPF v1.0 adapter mode: exact (default, faithful v1 behavior) or cutoff_safe (truncate data)")
    parser.add_argument("--allow-v2-fallback", action="store_true", default=False, help="Allow LightGBM/TimesFM to fall back to 2.0 implementations when EPF v1 is unavailable")
    parser.add_argument("--allow-missing-models", action="store_true", default=False, help="Allow ledger pipeline to continue even if some models fail")
    parser.add_argument("--allow-equal-weight-fallback", action="store_true", default=False, help="Allow fusion to use equal weights when no period weights are available")
    parser.add_argument("--strict-classifier", action="store_true", default=False, help="Fail ledger_full if classifier fails")
    parser.add_argument("--force", action="store_true", default=False, help="Force retrain even if checkpoint exists")
    parser.add_argument("--ledger-root", default="outputs/ledger", help="Root directory for ledger files")
    parser.add_argument("--runs-root", default="outputs/runs", help="Root directory for daily run outputs")
    # Realtime cutoff
    parser.add_argument("--realtime-cutoff-hour", type=int, default=14, help="Cutoff hour for realtime models on D-1 (default: 14)")
    # Recent week boost
    parser.add_argument("--recent-week-boost", action="store_true", default=True, help="Enable recent-week boost in day_gate weighting (default: enabled)")
    parser.add_argument("--recent-week-max-gate", type=float, default=0.85, help="Maximum day_gate with recent-week boost (default: 0.85)")
    # TimeMixer tuning
    parser.add_argument("--timemixer-epochs", type=int, default=80, help="TimeMixer training epochs (default: 80)")
    parser.add_argument("--timemixer-patience", type=int, default=15, help="TimeMixer early stopping patience (default: 15)")
    parser.add_argument("--timemixer-batch-size", type=int, default=16, help="TimeMixer batch size (default: 16)")
    parser.add_argument("--timemixer-full-refit", action="store_true", default=True, help="Enable TimeMixer full refit on train+valid after early stopping (default: enabled)")
    parser.add_argument("--timemixer-seeds", type=int, default=42, help="TimeMixer random seed (default: 42)")
    return parser
