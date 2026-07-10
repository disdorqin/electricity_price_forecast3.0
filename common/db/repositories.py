"""
Repository layer — all DB read/write operations for the 3NF EFM3 ledger.

Free-text domains (stage, model, policy, ...) are stored as foreign keys to
``efm_dim_*`` tables. Names are resolved to surrogate ids here (via
:mod:`common.db.dimensions`), so callers keep passing human-readable strings.
Run-children no longer store ``target_date`` — it is derived by joining
``efm_runs``.

All functions take a pymysql Connection as first argument.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from pymysql.connections import Connection

from .dimensions import resolve_dim_id, dim_name
from .models import (
    RunRecord, PredictionRecord, FusionDecisionRecord,
    PostflightCheckRecord, DeliveryOutputRecord, RunEventRecord,
)

logger = logging.getLogger(__name__)


# ── Run CRUD ──────────────────────────────────────────────────────

def create_run(conn: Connection, run: RunRecord) -> str:
    """Insert a new run record. Returns run_id."""
    sql = """
        INSERT INTO efm_runs
            (run_id, target_date, chain_version, mode, git_sha, config_hash,
             status, delivery_status, exit_code, started_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            status=VALUES(status), started_at=VALUES(started_at),
            updated_at=NOW()
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            run.run_id, run.target_date, run.chain_version, run.mode,
            run.git_sha, run.config_hash, run.status, run.delivery_status,
            run.exit_code, run.started_at or datetime.now(),
        ))
    conn.commit()
    return run.run_id


def update_run_status(
    conn: Connection,
    run_id: str,
    status: str,
    delivery_status: Optional[str] = None,
    exit_code: Optional[int] = None,
) -> None:
    """Update run status and optionally delivery_status and exit_code."""
    sets = ["status=%s"]
    params: list = [status]
    if delivery_status is not None:
        sets.append("delivery_status=%s")
        params.append(delivery_status)
    if exit_code is not None:
        sets.append("exit_code=%s")
        params.append(exit_code)
    sets.append("finished_at=NOW()")
    params.append(run_id)

    sql = f"UPDATE efm_runs SET {', '.join(sets)} WHERE run_id=%s"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
    conn.commit()


def fetch_run_summary(conn: Connection, run_id: str) -> Optional[dict]:
    """Fetch a run record as dict."""
    sql = "SELECT * FROM efm_runs WHERE run_id=%s"
    with conn.cursor() as cursor:
        cursor.execute(sql, (run_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in cursor.description]
    return dict(zip(cols, row))


# ── Predictions ───────────────────────────────────────────────────

def insert_prediction(conn: Connection, pred: PredictionRecord) -> int:
    """Insert a single prediction. Returns row id."""
    stage_id = resolve_dim_id(conn, "stage", pred.stage, description=pred.stage)
    model_id = resolve_dim_id(conn, "model", pred.model_name, description=pred.model_name)
    sql = """
        INSERT INTO efm_predictions
            (run_id, hour_business, task, stage_id, model_id, model_version,
             pred_price, is_shadow, is_selected, selected_reason,
             cutoff_time, quality_flags)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            pred_price=VALUES(pred_price),
            is_shadow=VALUES(is_shadow),
            is_selected=VALUES(is_selected),
            selected_reason=VALUES(selected_reason),
            model_version=VALUES(model_version),
            cutoff_time=VALUES(cutoff_time)
    """
    qf = json.dumps(pred.quality_flags) if pred.quality_flags else None
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            pred.run_id, pred.hour_business, pred.task, stage_id, model_id,
            pred.model_version, pred.pred_price, pred.is_shadow, pred.is_selected,
            pred.selected_reason, pred.cutoff_time, qf,
        ))
        row_id = cursor.lastrowid
    conn.commit()
    return row_id


def bulk_insert_predictions(conn: Connection, preds: list[PredictionRecord]) -> int:
    """Insert multiple predictions. Returns count."""
    count = 0
    for p in preds:
        insert_prediction(conn, p)
        count += 1
    return count


def mark_selected_prediction(
    conn: Connection,
    run_id: str,
    target_date: str,
    hour_business: int,
    stage: str,
    reason: str,
) -> None:
    """Mark the prediction for given run/date/hour/stage as selected (final)."""
    stage_id = resolve_dim_id(conn, "stage", stage, description=stage)
    # First, unselect all for this run/hour (target_date no longer stored on
    # efm_predictions; derive via efm_runs for safety / readability).
    unselect_sql = """
        UPDATE efm_predictions p JOIN efm_runs r ON p.run_id=r.run_id
        SET p.is_selected=FALSE
        WHERE p.run_id=%s AND r.target_date=%s AND p.hour_business=%s
    """
    select_sql = """
        UPDATE efm_predictions p JOIN efm_runs r ON p.run_id=r.run_id
        SET p.is_selected=TRUE, p.selected_reason=%s
        WHERE p.run_id=%s AND r.target_date=%s AND p.hour_business=%s AND p.stage_id=%s
    """
    with conn.cursor() as cursor:
        cursor.execute(unselect_sql, (run_id, target_date, hour_business))
        cursor.execute(select_sql, (reason, run_id, target_date, hour_business, stage_id))
    conn.commit()


def fetch_predictions(
    conn: Connection,
    run_id: str,
    task: Optional[str] = None,
    stage: Optional[str] = None,
    is_selected: Optional[bool] = None,
) -> list[dict]:
    """Fetch predictions. Returns list of dicts with ``stage``/``model_name`` joined in."""
    sql = """
        SELECT p.*, r.target_date AS target_date, s.name AS stage, m.name AS model_name
        FROM efm_predictions p
        JOIN efm_runs r       ON p.run_id = r.run_id
        JOIN efm_dim_stage s  ON p.stage_id = s.id
        JOIN efm_dim_model m  ON p.model_id  = m.id
        WHERE p.run_id=%s
    """
    params: list = [run_id]
    if task:
        sql += " AND p.task=%s"
        params.append(task)
    if stage:
        stage_id = resolve_dim_id(conn, "stage", stage, description=stage)
        sql += " AND p.stage_id=%s"
        params.append(stage_id)
    if is_selected is not None:
        sql += " AND p.is_selected=%s"
        params.append(is_selected)
    sql += " ORDER BY p.hour_business"

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


def fetch_selected_predictions(conn: Connection, run_id: str) -> list[dict]:
    """Fetch all selected (final) predictions for a run."""
    return fetch_predictions(conn, run_id, is_selected=True)


# ── Fusion Decisions ──────────────────────────────────────────────

def insert_fusion_decision(conn: Connection, decision: FusionDecisionRecord) -> int:
    policy_id = resolve_dim_id(conn, "policy", decision.policy_name, decision.policy_name)
    base_model_id = resolve_dim_id(conn, "model", decision.base_model, decision.base_model)
    selected_model_id = resolve_dim_id(conn, "model", decision.selected_model, decision.selected_model)
    sql = """
        INSERT INTO efm_fusion_decisions
            (run_id, hour_business, policy_id, base_model_id, selected_model_id,
             selected_prediction_id, decision_reason, decision_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            selected_model_id=VALUES(selected_model_id),
            selected_prediction_id=VALUES(selected_prediction_id),
            decision_reason=VALUES(decision_reason)
    """
    dj = json.dumps(decision.decision_json) if decision.decision_json else None
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            decision.run_id, decision.hour_business, policy_id, base_model_id,
            selected_model_id, decision.selected_prediction_id,
            decision.decision_reason, dj,
        ))
        row_id = cursor.lastrowid
    conn.commit()
    return row_id


# ── Postflight Checks ─────────────────────────────────────────────

def insert_postflight_check(conn: Connection, check: PostflightCheckRecord) -> int:
    check_id = resolve_dim_id(conn, "check", check.check_name, check.check_name)
    sql = """
        INSERT INTO efm_postflight_checks
            (run_id, check_id, passed, details)
        VALUES (%s,%s,%s,%s)
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (check.run_id, check_id, check.passed, check.details))
        row_id = cursor.lastrowid
    conn.commit()
    return row_id


# ── Delivery Outputs ──────────────────────────────────────────────

def insert_delivery_output(conn: Connection, output: DeliveryOutputRecord) -> int:
    output_type_id = resolve_dim_id(conn, "output", output.output_type, output.output_type)
    sql = """
        INSERT INTO efm_delivery_outputs
            (run_id, output_type_id, output_path, file_hash, row_count)
        VALUES (%s,%s,%s,%s,%s)
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            output.run_id, output_type_id, output.output_path,
            output.file_hash, output.row_count,
        ))
        row_id = cursor.lastrowid
    conn.commit()
    return row_id


# ── Run Events ────────────────────────────────────────────────────

def insert_run_event(conn: Connection, event: RunEventRecord) -> int:
    event_type_id = resolve_dim_id(conn, "event", event.event_type, event.event_type)
    sql = """
        INSERT INTO efm_run_events
            (run_id, event_type_id, event_name, event_detail, event_json)
        VALUES (%s,%s,%s,%s,%s)
    """
    ej = json.dumps(event.event_json) if event.event_json else None
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            event.run_id, event_type_id, event.event_name,
            event.event_detail, ej,
        ))
        row_id = cursor.lastrowid
    conn.commit()
    return row_id
