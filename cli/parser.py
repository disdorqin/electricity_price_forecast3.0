from __future__ import annotations

import argparse
from datetime import datetime


def _parse_yyyy_mm_dd(value: str, parser: argparse.ArgumentParser, field_name: str) -> str:
    """Validate and return a YYYY-MM-DD date string. Raises parser.error on failure."""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        parser.error(
            f"Invalid date for {field_name}: '{value}'. "
            f"Expected YYYY-MM-DD format (e.g. 2026-02-24)."
        )


def normalize_date_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """
    Normalize date-related arguments after parse_args().

    Handles:
      - Single positional  → args.date = value, pipeline = ledger_full
      - Two positionals    → args.start/end = values, pipeline = ledger_full_range
      - --start/--end      → auto-switch to ledger_full_range if not explicitly set
      - Conflict detection  → parser.error(...)
      - Date validation    → ensures YYYY-MM-DD format
    """
    # Validate date format for any provided date values
    if args.pos_date is not None:
        args.pos_date = _parse_yyyy_mm_dd(args.pos_date, parser, "pos_date")
    if args.pos_end is not None:
        args.pos_end = _parse_yyyy_mm_dd(args.pos_end, parser, "pos_end")
    if args.date is not None:
        args.date = _parse_yyyy_mm_dd(args.date, parser, "--date")
    if args.start is not None:
        args.start = _parse_yyyy_mm_dd(args.start, parser, "--start")
    if args.end is not None:
        args.end = _parse_yyyy_mm_dd(args.end, parser, "--end")

    # --- Conflict detection ---
    has_range_args = args.start is not None or args.end is not None

    if args.pos_date is not None and args.pos_end is not None:
        # Two positionals
        if args.date is not None:
            parser.error("Cannot use both positional dates and --date")
        if has_range_args:
            parser.error("Cannot use both positional dates and --start/--end")
        args.start = args.pos_date
        args.end = args.pos_end
        args.pipeline = "ledger_full_range"

    elif args.pos_date is not None:
        # Single positional
        if args.date is not None:
            parser.error("Cannot use both positional date and --date")
        if has_range_args:
            parser.error("Cannot use positional date together with --start/--end")
        args.date = args.pos_date

    # Explicit --date conflicts with --start/--end
    if args.date is not None and has_range_args:
        parser.error("Cannot use --date together with --start/--end")

    # Auto-switch to range mode when --start/--end are provided with default pipeline
    if has_range_args:
        if not args.start or not args.end:
            parser.error("Range mode requires both --start and --end")
        if args.pipeline == "ledger_full":
            args.pipeline = "ledger_full_range"

    # --- Pipeline-specific validations ---
    if args.pipeline == "ledger_full_range":
        if not args.start or not args.end:
            parser.error("ledger_full_range requires --start and --end (or two positional dates)")
        # Validate start <= end using parsed dates
        if datetime.strptime(args.start, "%Y-%m-%d") > datetime.strptime(args.end, "%Y-%m-%d"):
            parser.error(f"--start ({args.start}) must be <= --end ({args.end})")
    elif args.pipeline in ("ledger_full", "ledger_predict", "ledger_weight",
                           "ledger_fuse", "ledger_classifier", "ledger_smoke",
                           "extreme_price_shadow"):
        if not args.date:
            parser.error(f"--pipeline {args.pipeline} requires --date (or positional date)")
    elif args.pipeline == "ledger_backfill":
        if not args.start or not args.end:
            parser.error("ledger_backfill requires --start and --end")


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
            "extreme_price_shadow",
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
    # Hidden compatibility knob: LightGBM/TimesFM are always run through bundled 1.0-compatible adapters.
    # Kept only so old scripts that pass this flag do not break; deployment users should not see or set it.
    parser.add_argument("--epf-v1-mode", default="exact", choices=["exact", "cutoff_safe"], help=argparse.SUPPRESS)
    parser.add_argument("--allow-v2-fallback", action="store_true", default=False, help="Allow LightGBM/TimesFM to fall back to 2.0")
    parser.add_argument("--allow-missing-models", action="store_true", default=False, help="Continue even if some models fail")
    parser.add_argument("--allow-equal-weight-fallback", action="store_true", default=False, help="Use equal weights when no period weights available")
    parser.add_argument("--strict-classifier", action="store_true", default=False, help="Fail ledger_full if classifier fails")
    parser.add_argument("--ledger-root", default="outputs/ledger", help="Root directory for ledger files")
    parser.add_argument("--runs-root", default="outputs/runs", help="Root directory for daily run outputs")
    parser.add_argument("--realtime-cutoff-hour", type=int, default=14, help="Cutoff hour for realtime models on D-1")
    parser.add_argument("--recent-week-boost", dest="recent_week_boost", action="store_true", default=True, help="Enable recent-week boost in day_gate weighting")
    parser.add_argument("--no-recent-week-boost", dest="recent_week_boost", action="store_false", help="Disable recent-week boost")
    parser.add_argument("--recent-week-max-gate", type=float, default=0.85, help="Maximum day_gate with recent-week boost")
    parser.add_argument("--weight-max-lookback-days", type=int, default=90, help="Maximum calendar days to look back when selecting complete realtime training days (default 90)")

    # --- P3.2 Extreme Price Shadow (controlled, default OFF) ---
    parser.add_argument(
        "--enable-extreme-price-shadow", action="store_true", default=False,
        help="Run the P3.2 Extreme Price Correction as a CONTROLLED SHADOW (default OFF). "
             "Writes ONLY to outputs/runs/{date}/extreme_price_shadow/; never writes "
             "submission_ready.csv or final outputs. Safe no-op unless explicitly set.",
    )
    parser.add_argument(
        "--shadow-only", action="store_true", default=False,
        help="Reaffirm shadow-only observation mode. The shadow is ALWAYS shadow-only "
             "(corrected values never replace the original fused realtime prediction); "
             "this flag makes the intent explicit.",
    )
    parser.add_argument(
        "--extreme-price-shadow-config", default=None,
        help="Path to extreme_price_shadow.yaml (defaults to configs/extreme_price_shadow.yaml).",
    )

    # TimeMixer tuning
    parser.add_argument("--timemixer-epochs", type=int, default=80)
    parser.add_argument("--timemixer-patience", type=int, default=15)
    parser.add_argument("--timemixer-batch-size", type=int, default=16)
    parser.add_argument("--timemixer-full-refit", action="store_true", default=True)
    parser.add_argument("--timemixer-seeds", type=int, default=42)

    # --- Data sync parameters ---
    parser.add_argument(
        "--sync-data-before-run",
        action="store_true",
        default=False,
        help="Run sync_dataset before ledger_full / ledger_full_range.",
    )
    parser.add_argument(
        "--sync-source",
        default="auto",
        choices=["auto", "db", "http", "local"],
        help="Data sync source. auto = db first, then http/local fallback.",
    )
    parser.add_argument(
        "--force-sync",
        action="store_true",
        default=False,
        help="Refresh canonical dataset even if local data exists.",
    )
    parser.add_argument(
        "--require-fresh-data",
        action="store_true",
        default=False,
        help="Fail if synced/local dataset is not fresh enough for the requested target date.",
    )
    parser.add_argument(
        "--max-data-lag-hours",
        type=int,
        default=36,
        help="Maximum allowed lag between target decision time and latest available data.",
    )

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

    # Realtime DA-SGDF Selector Shadow params (default off)
    parser.add_argument("--enable-realtime-da-sgdf-selector-shadow",
        action="store_true", default=False,
        help="Enable the realtime DA-SGDF conservative selector shadow adapter. "
             "Default off. When enabled, writes shadow output to "
             "outputs/runs/YYYY-MM-DD/realtime_da_sgdf_selector_shadow/. "
             "Does NOT modify final, submission_ready, or champion.")
    parser.add_argument("--realtime-selector-shadow-config",
        default="configs/realtime_da_sgdf_selector_shadow.yaml",
        help="Path to selector shadow config YAML (default: configs/realtime_da_sgdf_selector_shadow.yaml)")

    # ── DB-ledger / Full-chain flags (all default-off) ──
    parser.add_argument("--use-db", action="store_true", default=False,
        help="Enable MySQL ledger backend for predictions (default OFF).")
    parser.add_argument("--db-url", default=None,
        help="MySQL connection URL: mysql+pymysql://USER:PASS@HOST:PORT/DB. "
             "Can also set EFM3_DB_URL env var. Required for --mode formal.")
    parser.add_argument("--init-db", action="store_true", default=False,
        help="Initialize EFM3 database schema and exit.")
    parser.add_argument("--mode", default=None, choices=["dry_run", "shadow", "formal", "formal_sim"],
        help="Run mode: dry_run (file ledger, no submission), "
             "shadow (DB with diagnostics), formal (DB + submission export), "
             "formal_sim (formal strict guards, no submission export). "
             "Default: dry_run.")
    parser.add_argument("--chain", default=None,
        choices=["official", "seasonal_da_router", "production_circuit"],
        help="Prediction chain to use. 'official' = 3.0 default, "
             "'seasonal_da_router' = seasonal DA policy router, "
             "'production_circuit' = NEW full production circuit (DB Ledger V2: "
             "dayahead+realtime sub-chains, repair, fusion, classifier, "
             "task finals, delivery final; every step recorded). Default: official.")
    parser.add_argument("--export-submission", action="store_true", default=False,
        help="Export submission_ready.csv after run. Only effective in formal mode.")
    parser.add_argument("--export-report", action="store_true", default=False,
        help="Generate delivery report after run.")
    parser.add_argument("--allow-router-fallback", action="store_true", default=False,
        help="Allow router to fallback when DA anchor missing (default OFF). "
             "Only relevant in formal/formal_sim modes.")

    # ── Data update flags (all default-off) ──
    parser.add_argument("--update-data", action="store_true", default=False,
        help="Run data update/import before the main chain (default OFF).")
    parser.add_argument("--data-root", default=None,
        help="Override data source root path.")
    parser.add_argument("--data-source", default="all",
        choices=["all", "two_five_reference", "efm3_local_data"],
        help="Data source to scan/import: all, two_five_reference, or efm3_local_data. Default: all.")
    parser.add_argument("--scan-only", action="store_true", default=False,
        help="Scan data sources and register files without importing (default OFF).")
    parser.add_argument("--full-refresh", action="store_true", default=False,
        help="Re-import all files even if already imported (default OFF).")
    parser.add_argument("--target-start-date", default=None,
        help="Start date for data update range filter (YYYY-MM-DD).")
    parser.add_argument("--target-end-date", default=None,
        help="End date for data update range filter (YYYY-MM-DD).")
    return parser
