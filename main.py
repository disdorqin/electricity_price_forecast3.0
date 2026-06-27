from __future__ import annotations

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from cli.parser import build_parser
from pipelines.evaluate_pipeline import run_evaluate_pipeline
from pipelines.sync_dataset_pipeline import run_sync_dataset_pipeline

# Ledger production pipelines
from pipelines.ledger_predict import run_ledger_predict
from pipelines.ledger_backfill import run_ledger_backfill
from pipelines.ledger_weight import run_ledger_weight
from pipelines.ledger_fuse import run_ledger_fuse
from pipelines.ledger_classifier import run_ledger_classifier
from pipelines.ledger_full import run_ledger_full
from pipelines.ledger_full_range import run_ledger_full_range
from pipelines.ledger_smoke import run_ledger_smoke


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Global reproducibility: seed must be set before any model code runs
    from utils.reproducibility import set_global_seed

    set_global_seed(args.seed, args.deterministic)

    # Positional date shortcuts:
    #   python main.py 2026-02-24                    → single day, default pipeline
    #   python main.py 2026-02-24 2026-02-28          → range mode, default pipeline
    if args.pos_date is not None:
        if args.pos_end is not None:
            # Two positionals → range mode
            if args.start is not None or args.end is not None:
                parser.error("Cannot use both positional dates and --start/--end")
            if args.date is not None:
                parser.error("Cannot use both positional dates and --date")
            args.start = args.pos_date
            args.end = args.pos_end
            args.pipeline = "ledger_full_range"
        else:
            # Single positional → single day
            if args.date is not None:
                parser.error("Cannot use both positional date and --date")
            args.date = args.pos_date

    # Validate no ambiguous combinations
    if args.pipeline == "ledger_full_range":
        if not args.start or not args.end:
            parser.error("ledger_full_range requires --start and --end (or two positional dates)")
        if args.start > args.end:
            parser.error(f"--start ({args.start}) > --end ({args.end})")
    elif args.pipeline in ("ledger_full", "ledger_predict", "ledger_weight",
                           "ledger_fuse", "ledger_classifier", "ledger_smoke"):
        if not args.date:
            parser.error(f"--pipeline {args.pipeline} requires --date (or positional date)")
    elif args.pipeline == "ledger_backfill":
        if not args.start or not args.end:
            parser.error("ledger_backfill requires --start and --end")

    if args.pipeline == "evaluate":
        output_path = run_evaluate_pipeline(args)
        print(output_path)
        return 0
    if args.pipeline == "sync_dataset":
        output_path = run_sync_dataset_pipeline(args)
        print(output_path)
        return 0
    # --- Ledger production pipelines ---
    if args.pipeline == "ledger_predict":
        result = run_ledger_predict(args)
        print(f"ledger_predict complete: {result}")
        return 0
    if args.pipeline == "ledger_backfill":
        result = run_ledger_backfill(args)
        print(f"ledger_backfill complete: {result}")
        return 0
    if args.pipeline == "ledger_weight":
        result = run_ledger_weight(args)
        print(f"ledger_weight complete: {result}")
        return 0
    if args.pipeline == "ledger_fuse":
        result = run_ledger_fuse(args)
        print(f"ledger_fuse complete: {result}")
        return 0
    if args.pipeline == "ledger_classifier":
        result = run_ledger_classifier(args)
        print(f"ledger_classifier complete: {result}")
        return 0
    if args.pipeline == "ledger_full":
        result = run_ledger_full(args)
        print(f"ledger_full complete: {result}")
        return 0
    if args.pipeline == "ledger_full_range":
        result = run_ledger_full_range(args)
        print(f"ledger_full_range complete: {result}")
        return 0
    if args.pipeline == "ledger_smoke":
        result = run_ledger_smoke(args)
        print(f"ledger_smoke complete: {result}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
