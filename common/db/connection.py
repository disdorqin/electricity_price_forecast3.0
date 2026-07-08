"""
DB Connection management for EFM3 MySQL ledger.
Uses pymysql directly — simple, no ORM overhead.
Supports connection pooling via recreation pattern.
"""

from __future__ import annotations

import os
import logging
import urllib.parse
from typing import Optional

import pymysql
from pymysql.connections import Connection

logger = logging.getLogger(__name__)

_DEFAULT_ENV_VAR = "EFM3_DB_URL"
_DEFAULT_POOL_SIZE = 5
_DEFAULT_TIMEOUT = 10


class DbConnectionManager:
    """Manages MySQL connection lifecycle."""

    def __init__(
        self,
        db_url: Optional[str] = None,
        env_var: str = _DEFAULT_ENV_VAR,
        pool_size: int = _DEFAULT_POOL_SIZE,
        connect_timeout: int = _DEFAULT_TIMEOUT,
    ):
        self._db_url = db_url or os.environ.get(env_var, "")
        self._pool_size = pool_size
        self._connect_timeout = connect_timeout
        self._conn: Optional[Connection] = None

    @property
    def is_configured(self) -> bool:
        """Whether a DB URL is available."""
        return bool(self._db_url)

    @property
    def db_url(self) -> str:
        return self._db_url

    def _parse_url(self) -> dict:
        """Parse mysql+pymysql://user:pass@host:port/dbname into connection params."""
        url = self._db_url
        # Strip prefix
        for prefix in ["mysql+pymysql://", "mysql://", "pymysql://"]:
            if url.startswith(prefix):
                url = url[len(prefix):]
                break

        user_pass, host_part = url.split("@", 1) if "@" in url else ("root", url)
        user, password = user_pass.split(":", 1) if ":" in user_pass else (user_pass, "")
        host_port, database = host_part.split("/", 1) if "/" in host_part else (host_part, "efm3")
        host, port_str = host_port.split(":", 1) if ":" in host_port else (host_port, "3306")

        # URL-decode credentials/components so that a password containing
        # special characters (e.g. '#' encoded as '%23' per the config docs)
        # is passed to pymysql correctly. This keeps the documented
        # `mysql+pymysql://user:PASS%23@host:3306/db` form working for both
        # the legacy raw-pymysql chain and SQLAlchemy-based backends.
        return {
            "host": urllib.parse.unquote(host),
            "port": int(port_str),
            "user": urllib.parse.unquote(user),
            "password": urllib.parse.unquote(password),
            "database": urllib.parse.unquote(database),
            "connect_timeout": self._connect_timeout,
            "charset": "utf8mb4",
        }

    def get_connection(self) -> Connection:
        """Get or create a MySQL connection."""
        if self._conn is not None and self._conn.open:
            try:
                self._conn.ping(reconnect=True)
                return self._conn
            except Exception:
                self._conn = None

        params = self._parse_url()
        logger.info(f"Connecting to MySQL at {params['host']}:{params['port']}/{params['database']}")
        self._conn = pymysql.connect(**params)
        return self._conn

    def close(self):
        if self._conn is not None and self._conn.open:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def health_check(self) -> dict:
        """Quick health check. Returns dict with status and details."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return {"status": "ok", "db_url_prefix": self._db_url.split("@")[-1].split("/")[0] if "@" in self._db_url else "unknown"}
        except Exception as e:
            return {"status": "error", "error": str(e)}


def get_db_connection(
    db_url: Optional[str] = None,
    env_var: str = _DEFAULT_ENV_VAR,
    connect_timeout: int = _DEFAULT_TIMEOUT,
) -> Optional[Connection]:
    """Convenience: get a single-use connection. For long-lived, use DbConnectionManager."""
    url = db_url or os.environ.get(env_var, "")
    if not url:
        return None

    mgr = DbConnectionManager(db_url=url, connect_timeout=connect_timeout)
    return mgr.get_connection()


def health_check(db_url: Optional[str] = None) -> dict:
    """Quick health check for CLI use."""
    mgr = DbConnectionManager(db_url=db_url)
    return mgr.health_check()
