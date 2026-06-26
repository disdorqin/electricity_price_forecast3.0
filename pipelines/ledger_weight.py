"""
Ledger weight pipeline.

For a target day D, reads prediction ledger + actual ledger for
the window [D-30, D-1], builds a training table, and learns
per-(task, period) fusion weights using Daily Ledger GEF.

Output:
  outputs/runs/{D}/{task}/weight/
    ledger_training_table.csv
    weights.csv
    dynamic_weight_trace.csv
    candidate_metrics.csv
    coverage_report.csv
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from pipelines.prediction_ledger import (
    load_prediction_ledger,
    load_actual_ledger,
    build_ledger_training_table,
    check_ledger_coverage,
)
from fusion.learners.daily_ledger_gef import DailyLedgerGEF, GEFConfig

logger = logging.getLogger(__name__)

DAYAHEAD_MODELS = ["lightgbm", "timesfm", "timemixer"]
REALTIME_MODELS = ["timesfm", "sgdfnet", "timemixer", "rt916"]


def run_ledger_weight(args: Any) -> dict:
    """
    Main entry for --pipeline ledger_weight.

    Parameters
    ----------
    args : argparse.Namespace
        Must contain: date, ledger_root, runs_root, window_days (optional).

    Returns
    -------
    dict with weights manifest.
    """
    target_date = args.date
    if not target_date:
        raise ValueError("--date is required for ledger_weight")

    ledger_root = Path(getattr(args, "ledger_root", "outputs/ledger"))
    runs_root = Path(getattr(args, "runs_root", "outputs/runs"))
    window_days = getattr(args, "validation_days", 30)
    recent_week_boost = getattr(args, "recent_week_boost", True)
    recent_week_max_gate = getattr(args, "recent_week_max_gate", 0.85)
    allow_missing = getattr(args, "allow_missing_models", False)

    logger.info(f"=== ledger_weight: {target_date} (window={window_days}d) ===")

    D = pd.Timestamp(target_date)

    # Generate window days [D-30, D-1]
    window_days_list = []
    for i in range(1, window_days + 1):
        d = D - pd.Timedelta(days=i)
        window_days_list.append(d.strftime("%Y-%m-%d"))

    manifest = {
        "pipeline": "ledger_weight",
        "target_date": target_date,
        "window_start": window_days_list[-1],
        "window_end": window_days_list[0],
        "window_days": window_days,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "results": {},
        "warnings": [],
        "errors": [],
    }

    try:
        failed_tasks = []
        for task in ["dayahead", "realtime"]:
            task_result = _learn_weights_for_task(
                task=task,
                target_date=target_date,
                window_days_list=window_days_list,
                ledger_root=ledger_root,
                runs_root=runs_root,
                expected_models=DAYAHEAD_MODELS if task == "dayahead" else REALTIME_MODELS,
                recent_week_boost=recent_week_boost,
                recent_week_max_gate=recent_week_max_gate,
            )
            manifest["results"][task] = task_result
            if task_result.get("status") != "complete":
                failed_tasks.append(
                    f"{task}: {task_result.get('error', task_result.get('status'))}"
                )

        if failed_tasks:
            manifest["status"] = "failed"
            manifest["errors"].extend(failed_tasks)
        else:
            manifest["status"] = "complete"

        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()

        # Weight validation (only if not failed)
        if not failed_tasks:
            _validate_weights(manifest)

    except Exception as e:
        manifest["status"] = "failed"
        manifest["errors"].append(str(e))
        logger.exception(f"ledger_weight failed: {e}")

    # Write manifest
    manifest_path = runs_root / target_date / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    # Merge with existing manifest if present
    if manifest_path.exists():
        with open(manifest_path, "r") as f:
            existing = json.load(f)
        existing["weight_stage"] = manifest
        with open(manifest_path, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False, default=str)
    else:
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

    return manifest


def _learn_weights_for_task(
    task: str,
    target_date: str,
    window_days_list: list[str],
    ledger_root: Path,
    runs_root: Path,
    expected_models: list[str],
    recent_week_boost: bool = True,
    recent_week_max_gate: float = 0.85,
) -> dict:
    """Learn weights for a single task (dayahead or realtime)."""
    result = {"task": task, "status": "running"}

    # Load ledgers
    pred_ledger = load_prediction_ledger(ledger_root, task, window_days_list)
    act_ledger = load_actual_ledger(ledger_root, task, window_days_list)

    logger.info(
        f"[{task}] Loaded: {len(pred_ledger)} prediction rows, "
        f"{len(act_ledger)} actual rows"
    )

    if pred_ledger.empty:
        result["status"] = "failed"
        result["error"] = f"Prediction ledger is empty for {task}"
        return result

    if act_ledger.empty:
        result["status"] = "failed"
        result["error"] = f"Actual ledger is empty for {task}"
        return result

    # Build training table
    training = build_ledger_training_table(
        prediction_ledger=pred_ledger,
        actual_ledger=act_ledger,
        target_day=target_date,
        window_days=len(window_days_list),
        recent_week_boost=recent_week_boost,
        recent_week_max_gate=recent_week_max_gate,
    )

    result["training_rows"] = len(training)
    result["training_days"] = training["target_day"].nunique() if "target_day" in training.columns else 0
    result["day_gate_min"] = round(float(training["day_gate"].min()), 4)
    result["day_gate_max"] = round(float(training["day_gate"].max()), 4)

    logger.info(
        f"[{task}] Training table: {len(training)} rows, "
        f"day_gate [{result['day_gate_min']}, {result['day_gate_max']}]"
    )

    # Save training table
    weight_dir = runs_root / target_date / task / "weight"
    weight_dir.mkdir(parents=True, exist_ok=True)
    training.to_csv(weight_dir / "ledger_training_table.csv", index=False)

    # Coverage check
    coverage = check_ledger_coverage(
        pred_ledger, act_ledger, task, window_days_list, expected_models
    )
    coverage.to_csv(weight_dir / "coverage_report.csv", index=False)
    result["coverage"] = {
        "total_expected": len(window_days_list) * len(expected_models) * 24,
        "total_actual": len(training),
    }

    # Learn weights
    gef = DailyLedgerGEF(GEFConfig(window_days=len(window_days_list)))
    weights = gef.fit(training)

    # Save weights
    weights_df = gef.get_weights_df()
    weights_df.to_csv(weight_dir / "weights.csv", index=False)

    # Save trace
    trace_df = gef.get_trace_df()
    if not trace_df.empty:
        trace_df.to_csv(weight_dir / "dynamic_weight_trace.csv", index=False)

    # Save candidate metrics
    metrics_df = gef.get_candidate_metrics(training)
    metrics_df.to_csv(weight_dir / "candidate_metrics.csv", index=False)

    # Verify weights sum
    for (t, p), wdict in weights.items():
        s = sum(wdict.values())
        if abs(s - 1.0) > 0.01:
            result.setdefault("weight_sum_warnings", []).append(
                f"{t}/{p}: sum={s:.4f}"
            )

    result["status"] = "complete"
    result["n_weights"] = len(weights)
    result["weight_dir"] = str(weight_dir)

    return result


def _validate_weights(manifest: dict):
    """Validate weight sums and update order."""
    for task in ["dayahead", "realtime"]:
        task_result = manifest.get("results", {}).get(task, {})
        if not isinstance(task_result, dict):
            continue

        weight_dir = Path(task_result.get("weight_dir", ""))
        if not weight_dir.exists():
            continue

        # Check weights.csv
        weights_path = weight_dir / "weights.csv"
        if weights_path.exists():
            wdf = pd.read_csv(weights_path)
            for (t, p), grp in wdf.groupby(["task", "period"]):
                s = grp["weight"].sum()
                if abs(s - 1.0) > 0.01:
                    manifest.setdefault("warnings", []).append(
                        f"Weight sum {t}/{p}: {s:.4f} != 1.0"
                    )

        # Check update order in trace
        trace_path = weight_dir / "dynamic_weight_trace.csv"
        if trace_path.exists():
            tdf = pd.read_csv(trace_path)
            for (t, p), grp in tdf.groupby(["task", "period"]):
                ages = grp["age_days"].drop_duplicates().sort_values().values
                expected_order = sorted(ages)
                if not (ages == expected_order).all():
                    manifest.setdefault("warnings", []).append(
                        f"Trace order for {t}/{p}: {list(ages)}, expected {list(expected_order)}"
                    )
