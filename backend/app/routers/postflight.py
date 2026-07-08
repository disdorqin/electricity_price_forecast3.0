"""Postflight read endpoints (mounted under /api to avoid path clash with runs)."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pymysql.connections import Connection
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..db import get_db
from ..security import require_access
from ..services import run_service
from ..schemas.runs import PostflightCheck

router = APIRouter(prefix="/api", tags=["postflight"], dependencies=[Depends(require_access)])


@router.get("/runs/{run_id}/postflight", response_model=List[PostflightCheck])
def get_run_postflight(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return run_service.get_run_postflight(conn, run_id)
