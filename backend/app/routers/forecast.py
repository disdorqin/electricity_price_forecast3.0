"""
Forecast router — API endpoints for triggering and monitoring EFM3 predictions.

Endpoints:
  POST /api/forecast/trigger          — start a pipeline run
  GET  /api/forecast/{target_date}/status  — check job status
  GET  /api/forecast/{target_date}/outputs — get output file tree
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import forecast_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


class TriggerRequest(BaseModel):
    target_date: str
    mode: str = "formal_sim"
    force: bool = False


class TriggerResponse(BaseModel):
    job_id: Optional[str] = None
    status: str
    pid: Optional[int] = None
    log_file: Optional[str] = None
    error: Optional[str] = None


class StatusResponse(BaseModel):
    job_id: Optional[str] = None
    target_date: str
    status: str
    started_at: Optional[str] = None
    stages: Optional[dict] = None
    error_detail: Optional[str] = None
    detail: Optional[str] = None
    error: Optional[str] = None


class OutputsResponse(BaseModel):
    target_date: str
    root: Optional[str] = None
    file_count: int = 0
    files: list = []
    key_csvs: dict = {}
    delivery_path: Optional[str] = None
    error: Optional[str] = None


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_forecast(req: TriggerRequest) -> TriggerResponse:
    """Trigger a full pipeline run for the given target_date.

    The pipeline runs asynchronously in the conda epf-2 environment.
    Use the status endpoint to poll for completion.
    """
    result = forecast_service.trigger_forecast(
        target_date=req.target_date,
        mode=req.mode,
        force=req.force,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return TriggerResponse(**result)


@router.get("/{target_date}/status", response_model=StatusResponse)
async def get_forecast_status(target_date: str) -> StatusResponse:
    """Check the status of a forecast job.

    Returns job metadata, stage statuses, and any error details.
    """
    result = forecast_service.get_forecast_status(target_date)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return StatusResponse(**result)


@router.get("/{target_date}/outputs", response_model=OutputsResponse)
async def get_forecast_outputs(target_date: str) -> OutputsResponse:
    """Get the output file tree for a completed forecast.

    Returns all files under outputs/<date>/ with sizes, plus key CSV paths
    for quick access (predict/fuse/final/weight CSVs).
    """
    result = forecast_service.get_forecast_outputs(target_date)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return OutputsResponse(**result)
