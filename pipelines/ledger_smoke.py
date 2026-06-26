"""
Ledger SMOKE pipeline.

Quick validation pipeline that runs ALL models (dayahead + realtime)
with reduced parameters to verify the full prediction chain works
before committing to a 30-day backfill.

Uses separate output directories (outputs/smoke/) to avoid
contaminating the production ledger.

Does NOT run weight/fuse/classifier — only model predictions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_ledger_smoke(args: Any) -> dict:
    """
    Main entry for --pipeline ledger_smoke.

    Runs ledger_predict with smoke-optimized settings:
    - Separate output dirs: outputs/smoke/runs/{D}, outputs/smoke/ledger
    - Reduced training: default 3 months, timemixer epochs=3, patience=1
    - force=True by default
    - ALL models required (dayahead 3 + realtime 4)
    - Produces smoke_report.json
    """
    import copy

    target_date = args.date
    if not target_date:
        raise ValueError("--date is required for ledger_smoke")

    logger.info(f"=== ledger_smoke: {target_date} ===")

    # Build smoke-optimized args
    smoke_args = copy.copy(args)
    smoke_args.ledger_root = str(getattr(args, "ledger_root", "outputs/ledger")).replace(
        "outputs/ledger", "outputs/smoke/ledger"
    )
    smoke_args.runs_root = str(getattr(args, "runs_root", "outputs/runs")).replace(
        "outputs/runs", "outputs/smoke/runs"
    )
    smoke_args.force = True
    smoke_args.allow_missing_models = False

    # Reduce training params for speed (user can override via CLI)
    if not _user_overrode(args, "training_months"):
        smoke_args.training_months = getattr(args, "training_months", 3)
    if not _user_overrode(args, "timemixer_epochs"):
        smoke_args.timemixer_epochs = getattr(args, "timemixer_epochs", 3)
    if not _user_overrode(args, "timemixer_patience"):
        smoke_args.timemixer_patience = getattr(args, "timemixer_patience", 1)

    logger.info(
        f"Smoke config: training_months={smoke_args.training_months}, "
        f"timemixer_epochs={smoke_args.timemixer_epochs}, "
        f"timemixer_patience={smoke_args.timemixer_patience}"
    )

    # Run predict
    from pipelines.ledger_predict import run_ledger_predict

    predict_result = run_ledger_predict(smoke_args)

    # Build smoke report
    report = {
        "pipeline": "ledger_smoke",
        "target_date": target_date,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "smoke_config": {
            "training_months": smoke_args.training_months,
            "timemixer_epochs": smoke_args.timemixer_epochs,
            "timemixer_patience": smoke_args.timemixer_patience,
            "data_path": smoke_args.data_path,
            "epf_v1_root": getattr(smoke_args, "epf_v1_root", None),
        },
        "predict_result": predict_result,
        "checks": {},
    }

    # Run smoke checks
    _run_smoke_checks(predict_result, target_date, report)

    # Write report
    runs_root = Path(smoke_args.runs_root)
    run_dir = runs_root / target_date
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "smoke_report.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"Smoke complete: {report.get('smoke_status', 'unknown')}")

    return report


def _run_smoke_checks(predict_result: dict, target_date: str, report: dict):
    """Run validation checks on smoke predictions."""
    checks = {}
    all_ok = True

    for task in ["dayahead", "realtime"]:
        task_results = predict_result.get("results", {}).get(task, {})
        expected_models = {
            "dayahead": ["lightgbm", "timesfm", "timemixer"],
            "realtime": ["timesfm", "sgdfnet", "timemixer", "rt916"],
        }[task]

        for model in expected_models:
            model_info = task_results.get(model, {})
            status = model_info.get("status", "unknown")
            if status == "failed":
                checks[f"{task}/{model}"] = f"FAILED: {model_info.get('error', '')}"
                all_ok = False
            else:
                checks[f"{task}/{model}"] = f"OK ({model_info.get('rows', '?')} rows)"

        # Check row counts
        long_rows = predict_result.get("results", {}).get(f"{task}_long_rows", 0)
        expected_rows = {"dayahead": 72, "realtime": 96}[task]
        if long_rows != expected_rows:
            checks[f"{task}_row_count"] = f"FAIL: {long_rows} != {expected_rows}"
            all_ok = False
        else:
            checks[f"{task}_row_count"] = f"OK: {long_rows} rows"

    report["checks"] = checks
    report["smoke_status"] = "PASS" if all_ok else "FAIL"


def _user_overrode(args: Any, param_name: str) -> bool:
    """Check if user explicitly set a CLI param (heuristic)."""
    # argparse stores defaults; we check if the value differs from our smoke default
    smoke_defaults = {
        "training_months": 3,
        "timemixer_epochs": 3,
        "timemixer_patience": 1,
    }
    actual = getattr(args, param_name, None)
    return actual is not None and actual != smoke_defaults.get(param_name)
