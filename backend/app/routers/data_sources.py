"""Data source / source file / data-update-run read endpoints (mounted under /api)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymysql.connections import Connection
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..db import get_db
from ..security import require_access
from ..services import dataset_service
from ..schemas.datasets import DataSource, DataUpdateRun, SourceFile

router = APIRouter(prefix="/api", tags=["data_sources"], dependencies=[Depends(require_access)])


@router.get("/data-sources", response_model=List[DataSource])
def list_data_sources(conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return dataset_service.list_data_sources(conn)


@router.get("/source-files", response_model=List[SourceFile])
def list_source_files(
    source_id: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    conn: Optional[Connection] = Depends(get_db),
) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return dataset_service.list_source_files(conn, source_id=source_id, limit=limit)


@router.get("/data-update-runs", response_model=List[DataUpdateRun])
def list_data_update_runs(limit: int = Query(50, ge=1, le=500), conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return dataset_service.list_data_update_runs(conn, limit=limit)
