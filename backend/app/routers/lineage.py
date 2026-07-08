"""Lineage endpoints — the project's showcase innovation (mounted under /api)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pymysql.connections import Connection
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..db import get_db
from ..security import require_access
from ..services import lineage_service
from ..schemas.reports import LineageResponse

router = APIRouter(prefix="/api", tags=["lineage"], dependencies=[Depends(require_access)])


@router.get("/lineage/{run_id}", response_model=LineageResponse)
def lineage_run(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> Dict[str, Any]:
    """Return the lineage for all hours of a run (aggregated). The full graph is
    per-hour via /lineage/{run_id}/hour/{hour_business}."""
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    # Run-level summary: return router decisions + selected per hour.
    rows = lineage_service.lineage_run_summary(conn, run_id)
    if rows is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return rows


@router.get("/lineage/{run_id}/hour/{hour_business}", response_model=LineageResponse)
def lineage_hour(
    run_id: str, hour_business: int, conn: Optional[Connection] = Depends(get_db)
) -> Dict[str, Any]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    if not (1 <= hour_business <= 24):
        raise HTTPException(400, "hour_business must be 1..24")
    data = lineage_service.get_lineage(conn, run_id, hour_business)
    if not data:
        raise HTTPException(404, f"Lineage for run {run_id} hour {hour_business} not found")
    return data
