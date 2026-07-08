"""Ops and report API schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class OpRequest(BaseModel):
    target_date: Optional[str] = None
    source: Optional[str] = None
    data_source: Optional[str] = None
    scan_only: bool = False
    full_refresh: bool = False
    data_root: Optional[str] = None
    chain: Optional[str] = None
    with_p3_shadow: bool = False
    with_selector_shadow: bool = False
    report_dir: Optional[str] = None
    # Dangerous operations MUST set this to True explicitly.
    confirm: bool = False
    # Dangerous operations (formal / export) MUST carry a non-empty reason for audit.
    reason: Optional[str] = None


class OpResponse(BaseModel):
    action: str
    status: str
    exit_code: Optional[int] = None
    message: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ShadowSafetyReport(BaseModel):
    status: str = "unknown"
    shadow_selected_count: int = 0
    final_from_shadow_count: int = 0
    unsafe_run_count: int = 0
    detail: Optional[str] = None


class DbHealthReport(BaseModel):
    status: str = "unknown"
    db_url_prefix: Optional[str] = None
    table_count: Optional[int] = None
    tables: List[str] = []
    note: Optional[str] = None


class ReportRef(BaseModel):
    name: str
    available: bool = False
    path: Optional[str] = None
