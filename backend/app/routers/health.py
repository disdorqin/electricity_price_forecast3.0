"""Health endpoints — no DB required."""

from __future__ import annotations

import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends

from ..config import settings
from ..db import db_health
from ..security import require_access

router = APIRouter(prefix="/api/health", tags=["health"], dependencies=[Depends(require_access)])


@router.get("")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "efm3-control-plane",
        "app_env": settings.app_env,
        "db_configured": settings.db_configured,
        "ops_enabled": settings.ops_enabled,
        "timestamp": datetime.datetime.now().isoformat(),
    }


@router.get("/db")
def health_db() -> Dict[str, Any]:
    if not settings.db_configured:
        return {"status": "not_configured", "db_url_prefix": ""}
    try:
        res = db_health()
        res["status"] = "ok"
        return res
    except Exception as e:  # pragma: no cover - defensive
        return {"status": "error", "error": str(e)}


@router.get("/schema")
def health_schema() -> Dict[str, Any]:
    if not settings.db_configured:
        return {"status": "not_configured", "tables": []}
    try:
        from common.db.schema import list_tables

        # local import avoids requiring a connection if not wanted
        from ..db import db_connection

        with db_connection() as conn:
            if conn is None:
                return {"status": "no_db", "tables": []}
            tables = list_tables(conn)
        return {"status": "ok", "table_count": len(tables), "tables": tables}
    except Exception as e:  # pragma: no cover - defensive
        return {"status": "error", "error": str(e)}
