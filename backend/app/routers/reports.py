"""Report endpoints — shadow safety, DB health, report references."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pymysql.connections import Connection
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..db import get_db
from ..security import require_access
from ..services import report_service
from ..schemas.ops import DbHealthReport, ReportRef, ShadowSafetyReport

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(require_access)])


@router.get("/latest")
def latest_report() -> Dict[str, Any]:
    return report_service.latest_report()


@router.get("/run/{run_id}")
def run_report(run_id: str) -> Dict[str, Any]:
    return report_service.run_report(run_id)


@router.get("/available")
def available_reports() -> List[Dict[str, Any]]:
    return report_service.available_reports()


@router.get("/shadow-safety", response_model=ShadowSafetyReport)
def shadow_safety(conn: Optional[Connection] = Depends(get_db)) -> Dict[str, Any]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return report_service.shadow_safety(conn)


@router.get("/db-health", response_model=DbHealthReport)
def db_health(conn: Optional[Connection] = Depends(get_db)) -> Dict[str, Any]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return report_service.db_health(conn)
