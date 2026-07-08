"""
full_chain_orchestrator.py — EFM3 3.0 Full-Chain Prediction Pipeline Orchestrator.

Orchestrates the complete end-to-end prediction pipeline:

    1. generate_run_id
    2. sync/validate input data
    3. create feature snapshot
    4. day-ahead prediction
    5. realtime prediction
    6. candidate/shadow prediction
    7. seasonal DA router
    8. final selection (handled by seasonal DA router)
    9. postflight
    10. export
    11. update run status

Each step writes an event to ``efm_run_events`` (when DB is enabled).
The orchestrator tolerates individual step failures and produces a
comprehensive result dict.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from common.prediction_store import (
    create_prediction_store,
    MySQLPredictionStore,
    FilePredictionStore,
    PredictionStore,
)
from common.db.connection import DbConnectionManager
from common.db.repositories import (
    create_run,
    update_run_status,
    insert_run_event,
    fetch_run_summary,
)
from common.db.models import RunRecord, RunEventRecord
from pipelines.seasonal_da_router import run_seasonal_da_router
from pipelines.db_postflight import run_db_postflight
from pipelines.db_exporter import export_submission_ready

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
INPUT_XLSX = DATA_DIR / "shandong_pmos_hourly.xlsx"
OUTPUTS_DIR = Path(__file__).resolve().parents[1] / "outputs"
LEDGER_DIR = OUTPUTS_DIR / "ledger"
RUNS_DIR = OUTPUTS_DIR / "runs"
EXPORTS_DIR = OUTPUTS_DIR / "exports"
PREDICTION_STORE_DIR = OUTPUTS_DIR / "prediction_store"

CHAIN_VERSION = "3.0-full-chain-v1"

_STEP_ORDER = [
    "generate_run_id",
    "sync_validate_input",
    "feature_snapshot",
    "dayahead_prediction",
    "realtime_prediction",
    "candidate_shadow_prediction",
    "seasonal_da_router",
    "final_selection",
    "postflight",
    "export",
    "update_run_status",
]


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════


def _get_git_sha() -> str:
    """Return the current git SHA (first 8 chars), or 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=Path(__file__).resolve().parents[1],
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:8]
    except Exception:
        logger.warning("Could not determine git SHA — falling back to 'unknown'")
    return "unknown"


def _generate_run_id(target_date: str) -> str:
    """Create a run_id in ``efm3_{YYYYMMDD}_{sha8}_{timestamp_compact}`` format."""
    ymd = target_date.replace("-", "")
    sha = _get_git_sha()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"efm3_{ymd}_{sha}_{ts}"


def _write_event(
    db_mgr: DbConnectionManager | None,
    run_id: str,
    event_type: str,
    event_name: str,
    event_detail: Optional[str] = None,
    event_json: Optional[dict] = None,
) -> None:
    """Insert a run event into the DB (if DB is configured). Silently skip if not."""
    if db_mgr is None:
        return
    try:
        conn = db_mgr.get_connection()
        record = RunEventRecord(
            run_id=run_id,
            event_type=event_type,
            event_name=event_name,
            event_detail=event_detail,
            event_json=event_json,
        )
        insert_run_event(conn, record)
        conn.close()
    except Exception:
        logger.debug("Failed to write run event (non-fatal): %s/%s", event_type, event_name)


def _step_wrapper(
    step_name: str,
    db_mgr: DbConnectionManager | None,
    run_id: str,
    fn,
    *args,
    **kwargs,
) -> dict:
    """Wrap a single pipeline step with logging, event writing, and exception handling.

    Returns a step result dict: {"status": "ok"|"failed", "detail": str}.
    """
    start = time.monotonic()
    detail: str = ""
    status: str = "ok"

    try:
        result = fn(*args, **kwargs)
        detail = str(result) if result is not None else "completed"
        logger.info("[%s] Step '%s' succeeded: %s", run_id[:16], step_name, detail[:200])
    except Exception:
        detail = traceback.format_exc()
        status = "failed"
        logger.error("[%s] Step '%s' failed:\n%s", run_id[:16], step_name, detail)

    elapsed = time.monotonic() - start

    if db_mgr is not None:
        _write_event(
            db_mgr,
            run_id,
            "error" if status == "failed" else "step",
            step_name,
            event_detail=f"status={status} elapsed={elapsed:.2f}s",
            event_json={
                "step": step_name,
                "status": status,
                "detail": detail[:500] if status == "failed" else None,
                "elapsed_s": round(elapsed, 3),
            },
        )

    return {"status": status, "detail": detail}


def _compute_config_hash(config: Optional[dict]) -> Optional[str]:
    """Compute a short SHA-256 hex digest of the config dict."""
    if config is None:
        return None
    raw = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═════════════════════════════════════════════════════════════════════════════
# Step implementations
# ═════════════════════════════════════════════════════════════════════════════


def _step_generate_run_id(target_date: str) -> str:
    """Generate a unique run ID for this pipeline execution."""
    run_id = _generate_run_id(target_date)
    logger.info("Generated run_id: %s", run_id)
    return run_id


def _step_sync_validate_input(target_date: str) -> str:
    """Validate that the input data file exists and is readable."""
    if not INPUT_XLSX.exists():
        raise FileNotFoundError(
            f"Input data file not found: {INPUT_XLSX}. "
            "Run sync_dataset_pipeline first."
        )
    size_kb = INPUT_XLSX.stat().st_size / 1024
    detail = f"Input file OK: {INPUT_XLSX.name} ({size_kb:.1f} KB)"
    logger.info(detail)
    return detail


def _step_feature_snapshot(
    run_id: str,
    target_date: str,
    store: PredictionStore,
) -> str:
    """Read features from the input xlsx and persist a snapshot via the store."""
    import pandas as pd

    if not INPUT_XLSX.exists():
        raise FileNotFoundError(f"Cannot read features — {INPUT_XLSX} not found.")

    df = pd.read_excel(INPUT_XLSX)
    required_cols = {"hour_business", "ds"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(
            f"Input xlsx missing required columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        pred_row = {
            "hour_business": int(row.get("hour_business", 0)),
            "task": "feature_snapshot",
            "stage": "input_features",
            "model_name": "passthrough",
            "model_version": "raw",
            "pred_price": float(row.get("price", row.get("pred_price", 0.0))),
            "is_shadow": False,
            "is_selected": False,
            "selected_reason": None,
            "quality_flags": None,
        }
        rows.append(pred_row)

    count = store.write_predictions(run_id, target_date, rows)
    return f"Wrote {count} feature rows from {INPUT_XLSX.name}"


def _step_dayahead_prediction(
    run_id: str,
    target_date: str,
    store: PredictionStore,
) -> str:
    """Read existing DA predictions from the ledger CSV."""
    da_ledger_dir = LEDGER_DIR / "dayahead" / "prediction"
    csv_path = da_ledger_dir / "prediction_ledger.csv"
    parquet_path = da_ledger_dir / "prediction_ledger.parquet"

    df = _load_ledger_dataframe(csv_path, parquet_path)
    target_rows = df[df["target_day"] == target_date].copy() if not df.empty else df

    if target_rows.empty:
        logger.warning(
            "No DA ledger predictions found for target_date=%s in %s",
            target_date, da_ledger_dir,
        )
        return "No DA predictions found for target date"

    # Map ledger columns → store prediction format
    preds = _ledger_df_to_predictions(target_rows, task="dayahead")
    count = store.write_predictions(run_id, target_date, preds)
    return f"Wrote {count} DA predictions from ledger (total target rows: {len(target_rows)})"


def _load_ledger_dataframe(csv_path: Path, parquet_path: Path) -> "pd.DataFrame":
    """Load a prediction ledger DataFrame from parquet (preferred) or CSV."""
    import pandas as pd

    if parquet_path.exists():
        try:
            return pd.read_parquet(parquet_path)
        except Exception:
            logger.debug("Parquet load failed, falling back to CSV")

    if csv_path.exists():
        return pd.read_csv(csv_path)

    logger.warning("No ledger data found at %s or %s", parquet_path, csv_path)
    return pd.DataFrame()


def _ledger_df_to_predictions(
    df: "pd.DataFrame",
    task: str,
    stage: str = "raw_model",
) -> list[dict[str, Any]]:
    """Convert a ledger DataFrame into a list of prediction dicts for the store."""
    import pandas as pd

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        hb = int(row.get("hour_business", row.get("hb", 0)))
        pred_price = float(row.get("y_pred", row.get("pred_price", 0.0)))
        model_name = str(row.get("model_name", "unknown"))
        model_version = str(row.get("model_version", "unknown"))

        pred = {
            "hour_business": hb,
            "task": task,
            "stage": stage,
            "model_name": model_name,
            "model_version": model_version,
            "pred_price": pred_price,
            "is_shadow": False,
            "is_selected": False,
            "selected_reason": None,
            "quality_flags": None,
        }
        rows.append(pred)
    return rows


def _step_realtime_prediction(
    run_id: str,
    target_date: str,
    store: PredictionStore,
) -> str:
    """Read existing RT predictions from run directories."""
    import pandas as pd

    # Look under outputs/runs/{target_date}/ for model-level CSVs
    rt_run_dir = RUNS_DIR / target_date
    if not rt_run_dir.exists():
        logger.warning("RT run directory not found: %s", rt_run_dir)
        return "No RT run directory found"

    model_predictions: list[dict[str, Any]] = []
    rt_subdirs = sorted(rt_run_dir.iterdir())

    for model_dir in rt_subdirs:
        if not model_dir.is_dir():
            continue
        for fname in ("predictions.csv", "prediction.csv", "results.csv"):
            csv_file = model_dir / fname
            if csv_file.exists():
                try:
                    mdf = pd.read_csv(csv_file)
                    model_name = model_dir.name
                    for _, row in mdf.iterrows():
                        hb = int(row.get("hour_business", row.get("hb", 0)))
                        pred_price = float(
                            row.get("y_pred", row.get("pred_price", row.get("prediction", 0.0)))
                        )
                        model_predictions.append({
                            "hour_business": hb,
                            "task": "realtime",
                            "stage": "raw_model",
                            "model_name": model_name,
                            "model_version": "unknown",
                            "pred_price": pred_price,
                            "is_shadow": False,
                            "is_selected": False,
                            "selected_reason": None,
                            "quality_flags": None,
                        })
                except Exception as exc:
                    logger.debug("Skipping %s: %s", csv_file, exc)
                break  # one CSV per directory

    if not model_predictions:
        logger.warning("No RT predictions found under %s", rt_run_dir)
        return "No RT predictions found"

    count = store.write_predictions(run_id, target_date, model_predictions)
    return f"Wrote {count} RT predictions from {rt_run_dir}"


def _step_candidate_shadow_prediction(
    run_id: str,
    target_date: str,
    store: PredictionStore,
    config: Optional[dict] = None,
) -> str:
    """Run P3/selector shadow candidates if enabled in config."""
    cfg = config or {}
    messages: list[str] = []

    # ── Extreme price shadow ────────────────────────────────────────────
    eps_enabled = cfg.get("extreme_price_shadow", {}).get("enabled", False)
    if eps_enabled:
        try:
            from pipelines.extreme_price_shadow import run_extreme_price_shadow

            eps_result = run_extreme_price_shadow(
                target_date=target_date,
                prediction_store=store,
                run_id=run_id,
                config=cfg.get("extreme_price_shadow", {}),
            )
            msgs = json.dumps(eps_result) if isinstance(eps_result, dict) else str(eps_result)
            messages.append(f"extreme_price_shadow: {msgs[:200]}")
            logger.info("Extreme price shadow completed for %s", target_date)
        except Exception as exc:
            logger.exception("Extreme price shadow failed (non-fatal): %s", exc)
            messages.append(f"extreme_price_shadow: FAILED ({exc})")
    else:
        messages.append("extreme_price_shadow: disabled")

    # ── RT DA-SGDF selector shadow ─────────────────────────────────────
    rds_enabled = cfg.get("realtime_da_sgdf_selector_shadow", {}).get("enabled", False)
    if rds_enabled:
        try:
            from pipelines.realtime_da_sgdf_selector_shadow import (
                run_realtime_da_sgdf_selector_shadow,
            )

            rds_result = run_realtime_da_sgdf_selector_shadow(
                target_date=target_date,
                prediction_store=store,
                run_id=run_id,
                config=cfg.get("realtime_da_sgdf_selector_shadow", {}),
            )
            msgs = json.dumps(rds_result) if isinstance(rds_result, dict) else str(rds_result)
            messages.append(f"realtime_da_sgdf_selector_shadow: {msgs[:200]}")
            logger.info("RT DA-SGDF selector shadow completed for %s", target_date)
        except Exception as exc:
            logger.exception("RT DA-SGDF selector shadow failed (non-fatal): %s", exc)
            messages.append(f"realtime_da_sgdf_selector_shadow: FAILED ({exc})")
    else:
        messages.append("realtime_da_sgdf_selector_shadow: disabled")

    return "; ".join(messages)


def _step_seasonal_da_router(
    run_id: str,
    target_date: str,
    store: PredictionStore,
) -> dict:
    """Delegate to the seasonal DA router for final selection."""
    router_result = run_seasonal_da_router(
        target_date=target_date,
        prediction_store=store,
        run_id=run_id,
    )
    logger.info(
        "Seasonal DA router result: status=%s hours_decided=%d",
        router_result.get("status"),
        router_result.get("hours_decided", 0),
    )
    return router_result


def _step_postflight(
    run_id: str,
    target_date: str,
    db_mgr: DbConnectionManager | None,
) -> str:
    """Run DB-based postflight checks if DB is available."""
    if db_mgr is None:
        msg = "DB not configured — skipping postflight checks"
        logger.info(msg)
        return msg

    try:
        conn = db_mgr.get_connection()
        pf_result = run_db_postflight(conn, run_id, target_date)
        conn.close()
        status = pf_result.get("status", "unknown")
        checks = pf_result.get("checks", {})
        passed = sum(1 for c in checks.values() if c.get("passed"))
        total = len(checks)
        msg = f"Postflight status={status} ({passed}/{total} checks passed)"
        logger.info(msg)
        return msg
    except Exception:
        logger.exception("Postflight step failed")
        raise


def _step_export(
    run_id: str,
    target_date: str,
    store: PredictionStore,
    use_db: bool,
) -> str:
    """Export submission-ready CSV via the PredictionStore's export method."""
    exports_dir = EXPORTS_DIR / target_date
    exports_dir.mkdir(parents=True, exist_ok=True)
    output_path = exports_dir / "submission_ready.csv"

    if use_db and isinstance(store, MySQLPredictionStore):
        result = export_submission_ready(
            run_id=run_id,
            target_date=target_date,
            prediction_store=store,
            output_dir=str(EXPORTS_DIR),
            is_formal=True,
        )
    else:
        result = export_submission_ready(
            run_id=run_id,
            target_date=target_date,
            prediction_store=store,
            output_dir=str(EXPORTS_DIR),
            is_formal=False,
        )

    out_path = result.get("output_path", str(output_path))
    row_count = result.get("row_count", 0)
    return f"Exported {row_count} rows to {out_path} (status={result.get('status')})"


def _determine_delivery_status(steps: dict[str, dict]) -> tuple[int, str]:
    """Determine the overall run status and delivery status from step results.

    Returns (exit_code, delivery_status).
    """
    all_ok = all(v["status"] == "ok" for v in steps.values())
    any_failed = any(v["status"] == "failed" for v in steps.values())
    all_failed = all(v["status"] == "failed" for v in steps.values())

    # Critical steps that must succeed for delivery
    critical_steps = [
        "dayahead_prediction",
        "realtime_prediction",
        "seasonal_da_router",
        "final_selection",
    ]
    critical_ok = all(steps.get(s, {}).get("status") == "ok" for s in critical_steps)

    if all_failed:
        return 2, "FAILED_NO_DELIVERY"
    if not critical_ok:
        return 2, "FAILED_NO_DELIVERY"
    if all_ok:
        return 0, "NORMAL"
    if any_failed:
        return 1, "DEGRADED_DELIVERED"
    return 0, "NORMAL"


def _compute_status_summary(
    steps: dict[str, dict],
    delivery_status: str,
) -> str:
    """Map delivery status to human-readable run status."""
    if delivery_status == "FAILED_NO_DELIVERY":
        return "FAIL"
    if delivery_status == "NORMAL":
        # All steps (or at least critical ones) passed
        if all(v["status"] == "ok" for v in steps.values()):
            return "COMPLETE"
        return "PARTIAL"
    if delivery_status == "DEGRADED_DELIVERED":
        return "PARTIAL"
    return "FAIL"


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════


def run_full_chain(
    target_date: str,
    mode: str = "dry_run",
    use_db: bool = False,
    db_url: str = "",
    export_submission: bool = False,
    export_report: bool = False,
    config: Optional[dict] = None,
) -> dict:
    """Execute the complete EFM3 3.0 prediction pipeline.

    Parameters
    ----------
    target_date : str
        Target business day in ``YYYY-MM-DD`` format.
    mode : str, default "dry_run"
        Execution mode (``"dry_run"`` or ``"production"``).
    use_db : bool, default False
        Whether to persist results to the MySQL database.
    db_url : str, default ""
        MySQL connection URL (e.g. ``mysql+pymysql://user:pass@host:3306/efm3``).
        Required when ``use_db=True``.
    export_submission : bool, default False
        Whether to write the submission-ready CSV at the end of the pipeline.
    export_report : bool, default False
        Whether to generate a delivery quality report (not yet implemented).
    config : dict or None, default None
        Optional configuration dict for shadow/candidate sub-pipelines.

    Returns
    -------
    dict
        ``{
            "run_id": str,
            "target_date": str,
            "mode": str,
            "status": "COMPLETE" | "PARTIAL" | "FAIL",
            "delivery_status": "NORMAL" | "DEGRADED_DELIVERED" | "FAILED_NO_DELIVERY",
            "exit_code": 0 | 1 | 2,
            "steps": {step_name: {"status": "ok"|"failed", "detail": str}},
            "runtime_s": float,
        }``
    """
    overall_start = time.monotonic()
    cfg = config or {}
    steps: dict[str, dict] = {}
    db_mgr: Optional[DbConnectionManager] = None

    # ── Resolve DB connection ────────────────────────────────────────────
    effective_db_url = db_url or os.environ.get("EFM3_DB_URL", "")
    if use_db and effective_db_url:
        try:
            db_mgr = DbConnectionManager(db_url=effective_db_url)
            # Verify connectivity
            test_conn = db_mgr.get_connection()
            test_conn.close()
            logger.info("DB connection established for run session")
        except Exception:
            logger.warning(
                "use_db=True but DB connection failed — falling back to file store"
            )
            db_mgr = None
    elif use_db and not effective_db_url:
        logger.warning(
            "use_db=True but no db_url provided and EFM3_DB_URL not set — "
            "falling back to file store"
        )
    else:
        logger.info("DB mode disabled — using file-based store")

    # ── Generate run_id ───────────────────────────────────────────────────
    run_id = _step_generate_run_id(target_date)
    steps["generate_run_id"] = {"status": "ok", "detail": run_id}

    # Write a 'start' event immediately
    if db_mgr is not None:
        _write_event(
            db_mgr,
            run_id,
            "start",
            "full_chain_start",
            event_detail=f"target_date={target_date} mode={mode}",
            event_json={
                "target_date": target_date,
                "mode": mode,
                "use_db": use_db,
                "export_submission": export_submission,
                "export_report": export_report,
                "chain_version": CHAIN_VERSION,
            },
        )

    # ── Create run record in DB ───────────────────────────────────────────
    if db_mgr is not None:
        try:
            conn = db_mgr.get_connection()
            run_record = RunRecord(
                run_id=run_id,
                target_date=target_date,
                chain_version=CHAIN_VERSION,
                mode=mode,
                git_sha=_get_git_sha(),
                config_hash=_compute_config_hash(cfg),
                status="PENDING",
                delivery_status="NOT_ATTEMPTED",
                exit_code=0,
                started_at=datetime.now(timezone.utc),
            )
            create_run(conn, run_record)
            conn.close()
            logger.info("Run record created in DB: %s", run_id)
        except Exception:
            logger.exception("Failed to create run record in DB (non-fatal)")

    # ── Create prediction store ───────────────────────────────────────────
    if use_db and db_mgr is not None and effective_db_url:
        store: PredictionStore = MySQLPredictionStore(db_url=effective_db_url)
        logger.info("Using MySQLPredictionStore for pipeline I/O")
    else:
        store = FilePredictionStore(base_dir=str(PREDICTION_STORE_DIR))
        logger.info("Using FilePredictionStore (base=%s)", PREDICTION_STORE_DIR)

    # ── Step 2: sync / validate input data ───────────────────────────────
    steps["sync_validate_input"] = _step_wrapper(
        "sync_validate_input", db_mgr, run_id, _step_sync_validate_input, target_date,
    )

    # ── Step 3: create feature snapshot ───────────────────────────────────
    steps["feature_snapshot"] = _step_wrapper(
        "feature_snapshot", db_mgr, run_id,
        _step_feature_snapshot, run_id, target_date, store,
    )

    # ── Step 4: day-ahead prediction ──────────────────────────────────────
    steps["dayahead_prediction"] = _step_wrapper(
        "dayahead_prediction", db_mgr, run_id,
        _step_dayahead_prediction, run_id, target_date, store,
    )

    # ── Step 5: realtime prediction ───────────────────────────────────────
    steps["realtime_prediction"] = _step_wrapper(
        "realtime_prediction", db_mgr, run_id,
        _step_realtime_prediction, run_id, target_date, store,
    )

    # ── Step 6: candidate / shadow prediction ─────────────────────────────
    steps["candidate_shadow_prediction"] = _step_wrapper(
        "candidate_shadow_prediction", db_mgr, run_id,
        _step_candidate_shadow_prediction, run_id, target_date, store, cfg,
    )

    # ── Step 7: seasonal DA router ────────────────────────────────────────
    router_step = _step_wrapper(
        "seasonal_da_router", db_mgr, run_id,
        _step_seasonal_da_router, run_id, target_date, store,
    )
    steps["seasonal_da_router"] = router_step

    # ── Step 8: final selection (implicitly handled by router above) ──────
    sel_detail = (
        f"handled by seasonal_da_router (status={router_step['status']})"
        if router_step["status"] == "ok"
        else "seasonal_da_router failed — no final selection performed"
    )
    steps["final_selection"] = {
        "status": router_step["status"],
        "detail": sel_detail,
    }

    # ── Step 9: postflight ────────────────────────────────────────────────
    steps["postflight"] = _step_wrapper(
        "postflight", db_mgr, run_id,
        _step_postflight, run_id, target_date, db_mgr,
    )

    # ── Step 10: export ───────────────────────────────────────────────────
    if export_submission:
        steps["export"] = _step_wrapper(
            "export", db_mgr, run_id,
            _step_export, run_id, target_date, store, use_db,
        )
    else:
        steps["export"] = {"status": "ok", "detail": "skipped (export_submission=False)"}

    # ── Determine exit / delivery status ──────────────────────────────────
    exit_code, delivery_status = _determine_delivery_status(steps)
    run_status = _compute_status_summary(steps, delivery_status)
    runtime_s = round(time.monotonic() - overall_start, 3)

    # ── Step 11: update run status ────────────────────────────────────────
    if db_mgr is not None:
        try:
            conn = db_mgr.get_connection()
            update_run_status(
                conn,
                run_id,
                status=run_status,
                delivery_status=delivery_status,
                exit_code=exit_code,
            )
            conn.close()
            logger.info(
                "Run status updated in DB: run_id=%s status=%s delivery=%s exit=%d",
                run_id, run_status, delivery_status, exit_code,
            )
            steps["update_run_status"] = {
                "status": "ok",
                "detail": f"status={run_status} delivery={delivery_status} exit={exit_code}",
            }
        except Exception:
            logger.exception("Failed to update run status in DB")
            steps["update_run_status"] = {"status": "failed", "detail": traceback.format_exc()}
    else:
        steps["update_run_status"] = {"status": "ok", "detail": "skipped (DB not enabled)"}

    # ── Write final 'complete' event ──────────────────────────────────────
    if db_mgr is not None:
        _write_event(
            db_mgr,
            run_id,
            "complete",
            "full_chain_complete",
            event_detail=(
                f"status={run_status} delivery={delivery_status} "
                f"runtime={runtime_s}s steps_ok={sum(1 for s in steps.values() if s['status']=='ok')}"
            ),
            event_json={
                "run_status": run_status,
                "delivery_status": delivery_status,
                "exit_code": exit_code,
                "runtime_s": runtime_s,
                "step_summary": {
                    name: s["status"] for name, s in steps.items()
                },
            },
        )

    # ── Build result ──────────────────────────────────────────────────────
    result: dict[str, Any] = {
        "run_id": run_id,
        "target_date": target_date,
        "mode": mode,
        "status": run_status,
        "delivery_status": delivery_status,
        "exit_code": exit_code,
        "steps": steps,
        "runtime_s": runtime_s,
    }

    logger.info(
        "Full-chain complete: run_id=%s status=%s delivery=%s exit=%d runtime=%.2fs",
        run_id, run_status, delivery_status, exit_code, runtime_s,
    )

    return result
