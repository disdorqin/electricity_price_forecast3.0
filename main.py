from __future__ import annotations

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from cli.parser import build_parser, normalize_date_args
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


def _delivery_exit_code(delivery_status: str, default: int = 1) -> int:
    """Map delivery status to exit code.

    NORMAL           -> 0
    DEGRADED_DELIVERED -> 2
    FAILED_NO_DELIVERY -> 1 (also default)
    """
    if delivery_status == "NORMAL":
        return 0
    elif delivery_status == "DEGRADED_DELIVERED":
        return 2
    elif delivery_status == "FAILED_NO_DELIVERY":
        return 1
    return default


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Normalize date arguments (handles positional <-> --date/--start/--end mapping)
    normalize_date_args(args, parser)

    # Global reproducibility: seed must be set before any model code runs
    from utils.reproducibility import set_global_seed

    set_global_seed(args.seed, args.deterministic)

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
        ds = result.get("delivery_status", "UNKNOWN")
        exit_code = _delivery_exit_code(ds, default=1)
        print(f"ledger_full complete: delivery_status={ds}, exit_code={exit_code}")
        return exit_code
    if args.pipeline == "ledger_full_range":
        result = run_ledger_full_range(args)
        ds = result.get("delivery_status", "UNKNOWN")
        # Range logic: NORMAL/complete -> 0, DEGRADED -> 2, else -> 1
        range_status = result.get("status", "")
        if ds == "NORMAL":
            exit_code = 0
        elif ds == "DEGRADED_DELIVERED":
            exit_code = 2
        elif range_status in ("complete", "all_skipped") and ds != "FAILED_NO_DELIVERY":
            # complete/all_skipped without degraded delivery is normal
            exit_code = 0
        else:
            # partial / failed / preflight_failed / interrupted / FAILED_NO_DELIVERY
            exit_code = 1
        print(f"ledger_full_range complete: status={range_status}, "
              f"delivery_status={ds}, exit_code={exit_code}")
        return exit_code
    if args.pipeline == "ledger_smoke":
        result = run_ledger_smoke(args)
        print(f"ledger_smoke complete: {result}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
