"""
Schema initialization — create all EFM3 tables.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pymysql.connections import Connection

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parent.parent.parent / "db" / "schema_3nf.sql"


def init_schema(conn: Connection, schema_path: Optional[str] = None) -> dict:
    """
    Execute schema SQL to create all EFM3 tables.
    Returns dict with status and table_count.
    """
    path = Path(schema_path) if schema_path else SCHEMA_FILE
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")

    sql = path.read_text(encoding="utf-8")

    # Split by semicolons, filter empty statements
    statements = [s.strip() for s in sql.split(";") if s.strip()]

    created = 0
    errors = []

    with conn.cursor() as cursor:
        for stmt in statements:
            # Skip USE/DATABASE/comment-only statements for the cursor
            if stmt.upper().startswith("CREATE DATABASE") or stmt.upper().startswith("USE"):
                continue
            try:
                cursor.execute(stmt)
                created += 1
            except Exception as e:
                # Ignore "already exists" errors
                err_str = str(e).lower()
                if "already exists" in err_str or "duplicate" in err_str:
                    created += 1
                else:
                    errors.append(f"{stmt[:60]}...: {e}")

    conn.commit()
    return {"status": "ok" if not errors else "partial", "statements_executed": created, "errors": errors}


def list_tables(conn: Connection) -> list[str]:
    """List all EFM3 tables in the current database."""
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        return [row[0] for row in cursor.fetchall()]
