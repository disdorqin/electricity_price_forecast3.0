"""
Data classes representing DB rows for EFM3 tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RunRecord:
    run_id: str
    target_date: str
    chain_version: str = "3.0-db-ledger-v1"
    mode: str = "dry_run"
    git_sha: Optional[str] = None
    config_hash: Optional[str] = None
    status: str = "PENDING"
    delivery_status: str = "NOT_ATTEMPTED"
    exit_code: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


@dataclass
class PredictionRecord:
    run_id: str
    target_date: str
    hour_business: int
    task: str            # 'dayahead' | 'realtime' | 'fusion' | 'final' | 'shadow'
    stage: str           # 'raw_model' | 'da_anchor' | 'official_baseline' | 'seasonal_da_router' | ...
    model_name: str
    pred_price: float
    model_version: str = "unknown"
    is_shadow: bool = False
    is_selected: bool = False
    selected_reason: Optional[str] = None
    cutoff_time: Optional[datetime] = None
    quality_flags: Optional[dict] = None


@dataclass
class FusionDecisionRecord:
    run_id: str
    target_date: str
    hour_business: int
    policy_name: str
    selected_model: str
    selected_prediction_id: Optional[int] = None
    base_model: Optional[str] = None
    decision_reason: Optional[str] = None
    decision_json: Optional[dict] = None


@dataclass
class PostflightCheckRecord:
    run_id: str
    target_date: str
    check_name: str
    passed: bool
    details: Optional[str] = None


@dataclass
class DeliveryOutputRecord:
    run_id: str
    target_date: str
    output_type: str
    output_path: str
    file_hash: Optional[str] = None
    row_count: Optional[int] = None


@dataclass
class RunEventRecord:
    run_id: str
    event_type: str  # 'start' | 'step' | 'warning' | 'error' | 'complete'
    event_name: str
    event_detail: Optional[str] = None
    event_json: Optional[dict] = None
