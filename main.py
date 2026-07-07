from __future__ import annotations

import json
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
from pipelines.extreme_price_shadow import (
    run_ledger_extreme_price_shadow,
    run_extreme_price_shadow_safe,
)


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

    # --- Optional data sync before main pipeline ---
    sync_before = getattr(args, "sync_data_before_run", False)
    if sync_before and args.pipeline in ("ledger_full", "ledger_full_range"):
        sync_result = run_sync_dataset_pipeline(args)
        status = sync_result.get("status", "failed")
        if status != "ok":
            sync_errors = sync_result.get("errors", ["sync_dataset failed"])
            print(f"ERROR: --sync-data-before-run: sync_dataset failed: {'; '.join(sync_errors)}", flush=True)
            return 1
        # Point data_path to the synced canonical xlsx so downstream
        # pipelines use the fresh data without the user having to pass it.
        synced_xlsx = sync_result.get("output_xlsx")
        if synced_xlsx:
            args.data_path = synced_xlsx
        print(f"sync_dataset: OK (source={sync_result.get('source', '?')}, rows={sync_result.get('rows', 0)})", flush=True)

    exit_code = _dispatch_pipeline(args)

    # --- P3.2 controlled shadow post-step (default OFF) ---
    # Runs only when --enable-extreme-price-shadow is explicitly passed. It reads
    # realtime fused predictions from the 3.0 run + ledger and writes ONLY to
    # outputs/runs/{date}/extreme_price_shadow/. It never writes final/ or
    # submission_ready.csv, never replaces the original fused realtime prediction,
    # and failures are caught here so the main chain is never affected.
    if getattr(args, "enable_extreme_price_shadow", False):
        try:
            shadow_manifest = run_extreme_price_shadow_safe(args)
            logger = logging.getLogger(__name__)
            logger.info(
                f"[extreme_price_shadow] status={shadow_manifest.get('status')} | "
                f"shadow_only={shadow_manifest.get('shadow_only')} | "
                f"final_contaminated={shadow_manifest.get('final_contaminated', False)} | "
                f"main_chain_affected={shadow_manifest.get('main_chain_affected', False)}"
            )
        except Exception as e:  # pragma: no cover - defensive
            logging.getLogger(__name__).exception(
                f"[extreme_price_shadow] hook error (main chain untouched): {e}"
            )
    return exit_code


def _dispatch_pipeline(args) -> int:
    """Dispatch to the selected pipeline. Returns the process exit code."""
    if args.pipeline == "evaluate":
        output_path = run_evaluate_pipeline(args)
        print(output_path)
        return 0
    if args.pipeline == "sync_dataset":
        output_path = run_sync_dataset_pipeline(args)
        status = output_path.get("status", "failed") if isinstance(output_path, dict) else "ok"
        print(json.dumps(output_path, indent=2, ensure_ascii=False) if isinstance(output_path, dict) else output_path)
        return 0 if status == "ok" or status == "skipped" else 1
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
    if args.pipeline == "extreme_price_shadow":
        # P3.2 controlled shadow (default OFF; only reached when explicitly selected)
        result = run_ledger_extreme_price_shadow(args)
        print(f"extreme_price_shadow complete: {result}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
