from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified electricity forecast entrypoint")
    parser.add_argument(
        "pos_date", nargs="?", default=None,
        help="Target date (YYYY-MM-DD). Shortcut for --date with the default pipeline.",
    )
    parser.add_argument(
        "pos_end", nargs="?", default=None,
        help="Range end date (YYYY-MM-DD). If provided with pos_date, activates range mode.",
    )
    parser.add_argument(
        "--pipeline",
        default="ledger_full",
        choices=[
            "evaluate",
            "sync_dataset",
            # Ledger production pipelines
            "ledger_predict",
            "ledger_backfill",
            "ledger_weight",
            "ledger_fuse",
            "ledger_classifier",
            "ledger_full",
            "ledger_full_range",
            "ledger_smoke",
        ],
    )
    parser.add_argument("--target", default="both", choices=["dayahead", "realtime", "both"])
    parser.add_argument("--models", default="all", help="Comma-separated model names or all")
    parser.add_argument("--date", default=None, help="Single target day, YYYY-MM-DD")
    parser.add_argument("--start", default=None, help="Range start, YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Range end, YYYY-MM-DD")
    parser.add_argument("--data-path", default="data/shandong_pmos_hourly.xlsx")
    parser.add_argument("--max-cpu-workers", type=int, default=2)
    parser.add_argument("--max-gpu-workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--force", action="store_true", default=False, help="Force rerun even if cached outputs exist")

    # Ledger pipeline parameters
    parser.add_argument("--epf-v1-root", default=None, help="[Optional legacy compatibility] External EPF v1.0 root. Not required for normal ledger_full runs; local lightGBM/ and TimesFMBackend/ are used by default.")
    parser.add_argument("--epf-v1-mode", default="exact", choices=["exact", "cutoff_safe"], help="EPF v1.0 adapter mode")
    parser.add_argument("--allow-v2-fallback", action="store_true", default=False, help="Allow LightGBM/TimesFM to fall back to 2.0")
    parser.add_argument("--allow-missing-models", action="store_true", default=False, help="Continue even if some models fail")
    parser.add_argument("--allow-equal-weight-fallback", action="store_true", default=False, help="Use equal weights when no period weights available")
    parser.add_argument("--strict-classifier", action="store_true", default=False, help="Fail ledger_full if classifier fails")
    parser.add_argument("--ledger-root", default="outputs/ledger", help="Root directory for ledger files")
    parser.add_argument("--runs-root", default="outputs/runs", help="Root directory for daily run outputs")
    parser.add_argument("--realtime-cutoff-hour", type=int, default=14, help="Cutoff hour for realtime models on D-1")
    parser.add_argument("--recent-week-boost", action="store_true", default=True, help="Enable recent-week boost in day_gate weighting")
    parser.add_argument("--recent-week-max-gate", type=float, default=0.85, help="Maximum day_gate with recent-week boost")

    # TimeMixer tuning
    parser.add_argument("--timemixer-epochs", type=int, default=80)
    parser.add_argument("--timemixer-patience", type=int, default=15)
    parser.add_argument("--timemixer-batch-size", type=int, default=16)
    parser.add_argument("--timemixer-full-refit", action="store_true", default=True)
    parser.add_argument("--timemixer-seeds", type=int, default=42)

    # Smoke pipeline params
    parser.add_argument("--smoke-training-months", type=int, default=3)
    parser.add_argument("--smoke-timemixer-epochs", type=int, default=3)
    parser.add_argument("--smoke-timemixer-patience", type=int, default=1)

    # Range pipeline params
    parser.add_argument("--continue-on-error", action="store_true", default=False,
        help="Continue range pipeline even if a single day fails")
    parser.add_argument("--skip-existing-final", action="store_true", default=False,
        help="Skip days with verified submission_ready.csv already present")
    parser.add_argument("--range-preflight", dest="range_preflight", action="store_true", default=True,
        help="Run preflight checks before starting range pipeline")
    parser.add_argument("--no-range-preflight", dest="range_preflight", action="store_false",
        help="Skip preflight checks")
    return parser
