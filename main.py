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

# Realtime DA-SGDF Selector Shadow (default off)
from pipelines.realtime_da_sgdf_selector_shadow import (
    run_realtime_da_sgdf_selector_shadow,
    enable_shadow,
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


def _run_selector_shadow_if_enabled(args, pipeline_result) -> None:
    """Run the DA-SGDF selector shadow if the flag is set.

    This is a no-op unless --enable-realtime-da-sgdf-selector-shadow is passed.
    It never modifies exit_code, delivery_status, final, or submission_ready.
    """
    if not getattr(args, "enable_realtime_da_sgdf_selector_shadow", False):
        return
    enable_shadow()
    target_date = getattr(args, "date", None) or pipeline_result.get("target_date", "")
    if not target_date:
        target_date = getattr(args, "pos_date", None) or ""
    try:
        manifest = run_realtime_da_sgdf_selector_shadow(
            target_date=target_date,
            runs_root=getattr(args, "runs_root", "outputs/runs"),
            data_path=getattr(args, "data_path", "data/shandong_pmos_hourly.xlsx"),
            config_path=getattr(args, "realtime_selector_shadow_config", None),
        )
        print(f"[shadow-selector] manifest: status={manifest.get('status')}", flush=True)
    except Exception as e:
        print(f"[shadow-selector] WARNING: non-fatal error: {e}", flush=True)
    # NEVER modify exit_code — shadow failures must NOT affect main pipeline.


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Normalize date arguments (handles positional <-> --date/--start/--end mapping)
    normalize_date_args(args, parser)

    # Global reproducibility: seed must be set before any model code runs
    from utils.reproducibility import set_global_seed

    set_global_seed(args.seed, args.deterministic)

    # --- Production Circuit (DB Ledger V2) dedicated entry point ---
    # When --chain production_circuit is requested we MUST NOT run the legacy
    # ledger_full pipeline first (it trains/predicts models and would hang or
    # overlap with the circuit). Route straight to the circuit and return.
    chain = getattr(args, "chain", "official")
    if chain == "production_circuit":
        use_db = getattr(args, "use_db", False)
        mode = getattr(args, "mode", "dry_run")
        db_url = getattr(args, "db_url", None) or os.environ.get("EFM3_DB_URL", "")
        if not use_db:
            print("ERROR: --chain production_circuit requires --use-db", flush=True)
            return 1
        try:
            from pipelines.production_circuit import run_production_circuit
            target_date = getattr(args, "date", None) or getattr(args, "pos_date", None)
            if not target_date:
                print("ERROR: --date required for production_circuit", flush=True)
                return 1
            config = {
                "enable_p3_shadow": getattr(args, "enable_extreme_price_shadow", False),
                "enable_selector_shadow": getattr(args, "enable_realtime_da_sgdf_selector_shadow", False),
                "allow_router_fallback": getattr(args, "allow_router_fallback", False),
            }
            circ_result = run_production_circuit(
                target_date=target_date, mode=mode, use_db=use_db,
                db_url=db_url, config=config,
            )
            print(
                f"production_circuit: run_id={circ_result.get('run_id')} "
                f"status={circ_result.get('status')} "
                f"recommendation={circ_result.get('recommendation')} "
                f"smoke={circ_result.get('smoke_result')}", flush=True)
            return 0 if circ_result.get("status") == "COMPLETE" else 1
        except Exception as e:
            logging.getLogger(__name__).exception(f"[production_circuit] error: {e}")
            return 1

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

    # --- DB-ledger full-chain (default OFF) ---
    use_db = getattr(args, "use_db", False)
    mode = getattr(args, "mode", "dry_run")
    init_db = getattr(args, "init_db", False)
    db_url = getattr(args, "db_url", None) or os.environ.get("EFM3_DB_URL", "")

    if init_db:
        try:
            from common.db.connection import DbConnectionManager
            from common.db.schema import init_schema, list_tables
            mgr = DbConnectionManager(db_url=db_url)
            conn = mgr.get_connection()
            cursor = conn.cursor()
            cursor.execute("CREATE DATABASE IF NOT EXISTS efm3 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.execute("USE efm3")
            cursor.close()
            conn.commit()
            result = init_schema(conn)
            tables = list_tables(conn)
            print(f"Schema init: {result['status']} ({result['statements_executed']} statements)")
            print(f"Tables ({len(tables)}): {', '.join(tables)}")
            mgr.close()
            return 0 if result["status"] == "ok" else 1
        except Exception as e:
            print(f"ERROR --init-db: {e}")
            return 1

    # --- Data update (default OFF) ---
    update_data = getattr(args, "update_data", False)
    if update_data and (use_db or mode != "dry_run"):
        try:
            from pipelines.data_update_pipeline import run_data_update
            target_date = getattr(args, "date", None)
            if not target_date:
                target_date = getattr(args, "pos_date", None)
            update_result = run_data_update(
                target_date=target_date,
                source=getattr(args, "data_source", "all"),
                scan_only=getattr(args, "scan_only", False),
                full_refresh=getattr(args, "full_refresh", False),
                data_root=getattr(args, "data_root", None),
                db_url=db_url,
            )
            print(f"data_update: status={update_result.get('status')} files={update_result.get('files_detected', 0)}")
        except Exception as e:
            logging.getLogger(__name__).exception(f"[data_update] error: {e}")
            if mode == "formal":
                return 1

    if use_db or mode != "dry_run":
        try:
            target_date = getattr(args, "date", None)
            if not target_date:
                target_date = getattr(args, "pos_date", None)
            if not target_date:
                print("ERROR: --date required for DB-ledger chain")
                return 1

            from pipelines.full_chain_orchestrator import run_full_chain
            chain = getattr(args, "chain", "official")
            config = {
                "enable_p3_shadow": getattr(args, "enable_extreme_price_shadow", False),
                "enable_selector_shadow": getattr(args, "enable_realtime_da_sgdf_selector_shadow", False),
                "allow_router_fallback": getattr(args, "allow_router_fallback", False),
            }
            if chain == "production_circuit":
                # NEW full production circuit (DB Ledger V2). Additive; the
                # old seasonal_da_router chain is preserved (selected via
                # --chain seasonal_da_router / official).
                from pipelines.production_circuit import run_production_circuit
                circ_result = run_production_circuit(
                    target_date=target_date,
                    mode=mode,
                    use_db=use_db,
                    db_url=db_url,
                    config=config,
                )
                print(
                    f"production_circuit: run_id={circ_result.get('run_id')} "
                    f"status={circ_result.get('status')} "
                    f"recommendation={circ_result.get('recommendation')} "
                    f"smoke={circ_result.get('smoke_result')}"
                )
                # PARTIAL (realtime model missing) -> exit 1; COMPLETE -> 0.
                return 0 if circ_result.get("status") == "COMPLETE" else 1
            chain_result = run_full_chain(
                target_date=target_date,
                mode=mode,
                use_db=use_db,
                db_url=db_url,
                export_submission=getattr(args, "export_submission", False),
                export_report=getattr(args, "export_report", False),
                config=config,
            )
            print(f"full_chain: run_id={chain_result.get('run_id')} status={chain_result.get('status')} exit={chain_result.get('exit_code')}")
            return chain_result.get("exit_code", 0)
        except Exception as e:
            logging.getLogger(__name__).exception(f"[db_chain] error: {e}")
            return 1

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

        # --- Optional realtime DA-SGDF selector shadow (default off) ---
        _run_selector_shadow_if_enabled(args, result)

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

        # --- Optional realtime DA-SGDF selector shadow (default off) ---
        _run_selector_shadow_if_enabled(args, result)

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
