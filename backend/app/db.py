"""
DB dependency for FastAPI.

Yields a pymysql Connection from the configured EFM3_DB_URL, or ``None`` when the
database is not configured (so read-only endpoints can degrade gracefully instead
of crashing). The backend never constructs raw SQL with user input — all queries
use parameterized statements.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from pymysql.connections import Connection

from .config import settings
from common.db.connection import DbConnectionManager


@contextmanager
def db_connection() -> Iterator[Optional[Connection]]:
    """Yield a MySQL connection, or None if not configured. Always closes."""
    if not settings.db_url:
        yield None
        return
    mgr = DbConnectionManager(db_url=settings.db_url)
    conn = mgr.get_connection()
    try:
        yield conn
    finally:
        try:
            mgr.close()
        except Exception:
            pass


def get_db() -> Iterator[Optional[Connection]]:
    """FastAPI dependency — yields a DB connection (or None)."""
    with db_connection() as conn:
        yield conn


def db_health() -> dict:
    """Return a health dict for the configured DB (does not raise)."""
    if not settings.db_url:
        return {"status": "not_configured", "db_url_prefix": ""}
    mgr = DbConnectionManager(db_url=settings.db_url)
    return mgr.health_check()
