"""Prediction service — reads from efm_predictions."""

from __future__ import annotations

from typing import List, Optional

from pymysql.connections import Connection

from .base import q_all


def get_predictions(
    conn: Connection,
    run_id: str,
    task: Optional[str] = None,
    stage: Optional[str] = None,
    is_selected: Optional[bool] = None,
    limit: int = 2000,
) -> List[dict]:
    sql = (
        "SELECT p.*, s.name AS stage, m.name AS model_name "
        "FROM efm_predictions p "
        "JOIN efm_dim_stage s ON p.stage_id = s.id "
        "JOIN efm_dim_model m ON p.model_id = m.id "
        "WHERE p.run_id=%s"
    )
    params: list = [run_id]
    if task:
        sql += " AND p.task=%s"
        params.append(task)
    if stage:
        sql += " AND s.name=%s"
        params.append(stage)
    if is_selected is not None:
        sql += " AND p.is_selected=%s"
        params.append(1 if is_selected else 0)
    sql += " ORDER BY p.hour_business ASC, p.id ASC LIMIT %s"
    params.append(int(limit))
    return q_all(conn, sql, params)


def get_hourly(conn: Connection, run_id: str) -> List[dict]:
    return get_predictions(conn, run_id)


def get_selected(conn: Connection, run_id: str) -> List[dict]:
    return get_predictions(conn, run_id, is_selected=True)


def get_compare(conn: Connection, run_id: str, models: List[str]) -> List[dict]:
    """Return prediction rows for the requested model stages (for charting).

    ``models`` are interpreted as stage names (e.g. da_anchor, official_baseline,
    seasonal_da_router). Returns one row per (hour, model).
    """
    if not models:
        return []
    placeholders = ",".join(["%s"] * len(models))
    sql = (
        "SELECT p.*, s.name AS stage, m.name AS model_name "
        "FROM efm_predictions p "
        "JOIN efm_dim_stage s ON p.stage_id = s.id "
        "JOIN efm_dim_model m ON p.model_id = m.id "
        f"WHERE p.run_id=%s AND s.name IN ({placeholders}) "
        "ORDER BY p.hour_business ASC, s.name ASC"
    )
    params = [run_id, *models]
    return q_all(conn, sql, params)
