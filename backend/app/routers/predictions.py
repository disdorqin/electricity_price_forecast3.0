"""Prediction read endpoints (mounted under /api/runs)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymysql.connections import Connection
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..db import get_db
from ..security import require_access
from ..services import prediction_service
from ..schemas.predictions import HourlyPrediction, PredictionCompareItem, PredictionRow, SelectedPrediction

router = APIRouter(prefix="/api/runs", tags=["predictions"], dependencies=[Depends(require_access)])


@router.get("/{run_id}/predictions", response_model=List[PredictionRow])
def get_predictions(
    run_id: str,
    task: Optional[str] = None,
    stage: Optional[str] = None,
    selected_only: bool = False,
    limit: int = Query(2000, ge=1, le=10000),
    conn: Optional[Connection] = Depends(get_db),
) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    is_selected = True if selected_only else None
    return prediction_service.get_predictions(conn, run_id, task=task, stage=stage, is_selected=is_selected, limit=limit)


@router.get("/{run_id}/predictions/hourly", response_model=List[HourlyPrediction])
def get_hourly(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return prediction_service.get_hourly(conn, run_id)


@router.get("/{run_id}/predictions/selected", response_model=List[SelectedPrediction])
def get_selected(run_id: str, conn: Optional[Connection] = Depends(get_db)) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    return prediction_service.get_selected(conn, run_id)


@router.get("/{run_id}/predictions/compare", response_model=List[PredictionCompareItem])
def get_compare(
    run_id: str,
    models: str = Query("da_anchor,official_baseline,seasonal_da_router"),
    conn: Optional[Connection] = Depends(get_db),
) -> List[dict]:
    if conn is None:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database not configured")
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    return prediction_service.get_compare(conn, run_id, model_list)
