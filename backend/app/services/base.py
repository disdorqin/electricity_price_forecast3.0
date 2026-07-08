"""Shared DB query helpers for services (parameterized queries only)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional, Sequence

from pymysql.connections import Connection


def _serialize(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def q_all(conn: Connection, sql: str, params: Optional[Sequence] = None) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, (_serialize(v) for v in r))) for r in rows]


def q_one(conn: Connection, sql: str, params: Optional[Sequence] = None) -> Optional[dict]:
    rows = q_all(conn, sql, params)
    return rows[0] if rows else None


def q_scalar(conn: Connection, sql: str, params: Optional[Sequence] = None) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
    if row is None:
        return None
    return _serialize(row[0])
