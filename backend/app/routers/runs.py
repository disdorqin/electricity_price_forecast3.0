"""Run read endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymysql.connections import Connection
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..db import get_db
from ..security import require_access
from ..services import run_service
from ..schemas.runs import RunDetail, RunEvent, RunSummary
from ..schemas.predictions import PredictionRow

router = APIRouter(prefix="/api/runs", tags=["runs"], dependencies=[Depends(require_access)])


@router.get("")
def list_runs(
    limit: int = Query(50, ge=1, le=500),
    mode: Optional[str] = None,
    conn: Optional[Connection] = Depends(get_db),
) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return run_service.list_runs(conn, limit=limit, mode=mode)


@router.get("/{run_id}")
def get_run(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> dict:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    row = run_service.get_run_summary(conn, run_id)
    if not row:
        raise HTTPException(404, f"Run {run_id} not found")
    return row


@router.get("/{run_id}/summary", response_model=RunSummary)
def get_run_summary(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> dict:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    row = run_service.get_run_summary(conn, run_id)
    if not row:
        raise HTTPException(404, f"Run {run_id} not found")
    return row


@router.get("/{run_id}/events", response_model=List[RunEvent])
def get_run_events(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return run_service.get_run_events(conn, run_id)


@router.get("/{run_id}/delivery-outputs")
def get_run_delivery(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return run_service.get_run_delivery(conn, run_id)


@router.get("/{run_id}/detail", response_model=RunDetail)
def get_run_detail(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> dict:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    row = run_service.get_run_detail(conn, run_id)
    if not row:
        raise HTTPException(404, f"Run {run_id} not found")
    return row
