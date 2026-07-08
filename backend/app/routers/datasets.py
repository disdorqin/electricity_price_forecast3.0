"""Dataset / data-source read endpoints (mounted under /api)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymysql.connections import Connection
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..db import get_db
from ..security import require_access
from ..services import dataset_service
from ..schemas.datasets import (
    DatasetReadiness,
    DataSource,
    DataUpdateRun,
    DatasetVersion,
    SourceFile,
)

router = APIRouter(prefix="/api", tags=["datasets"], dependencies=[Depends(require_access)])


@router.get("/datasets", response_model=List[DatasetVersion])
def list_datasets(limit: int = Query(50, ge=1, le=500), conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return dataset_service.list_datasets(conn, limit=limit)


@router.get("/datasets/latest", response_model=Optional[DatasetVersion])
def get_latest_dataset(target_date: Optional[str] = None, conn: Optional[Connection] = Depends(get_db)):
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return dataset_service.get_latest_dataset(conn, target_date=target_date)


@router.get("/datasets/readiness", response_model=List[DatasetReadiness])
def dataset_readiness(conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    try:
        from ..services.base import q_all

        return q_all(conn, "SELECT dataset_id, target_date, status, row_counts, leakage_cutoff, canonical_hour_mapping FROM v_efm_dataset_readiness LIMIT 200")
    except Exception:
        return dataset_service.list_datasets(conn, limit=200)


@router.get("/datasets/{dataset_id}", response_model=DatasetVersion)
def get_dataset(dataset_id: str, conn: Optional[Connection] = Depends(get_db)) -> dict:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    row = dataset_service.get_dataset(conn, dataset_id)
    if not row:
        raise HTTPException(404, f"Dataset {dataset_id} not found")
    return row
