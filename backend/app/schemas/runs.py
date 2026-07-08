"""Run-related API schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class RunSummary(BaseModel):
    run_id: str
    target_date: str
    chain_version: Optional[str] = None
    mode: Optional[str] = None
    status: Optional[str] = None
    delivery_status: Optional[str] = None
    exit_code: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_s: Optional[float] = None


class RunEvent(BaseModel):
    event_type: Optional[str] = None
    event_name: Optional[str] = None
    event_detail: Optional[str] = None
    created_at: Optional[str] = None


class PostflightCheck(BaseModel):
    check_name: str
    passed: bool
    details: Optional[str] = None


class DeliveryOutput(BaseModel):
    output_type: Optional[str] = None
    output_path: Optional[str] = None
    row_count: Optional[int] = None
    file_hash: Optional[str] = None


class RunPredictionCounts(BaseModel):
    total: int = 0
    selected: int = 0
    shadow: int = 0


class RunDetail(BaseModel):
    summary: Optional[RunSummary] = None
    prediction_counts: Optional[RunPredictionCounts] = None
    postflight: List[PostflightCheck] = []
    delivery: List[DeliveryOutput] = []
    events_count: int = 0
