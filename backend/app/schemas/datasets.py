"""Dataset / data-source / lineage API schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class DataSource(BaseModel):
    source_id: str
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    market: Optional[str] = None
    root_path: Optional[str] = None
    enabled: Optional[bool] = None


class SourceFile(BaseModel):
    file_name: Optional[str] = None
    file_path: Optional[str] = None
    file_ext: Optional[str] = None
    file_size: Optional[int] = None
    file_sha256: Optional[str] = None
    import_status: Optional[str] = None
    import_message: Optional[str] = None
    detected_at: Optional[str] = None
    imported_at: Optional[str] = None


class DataUpdateRun(BaseModel):
    update_run_id: str
    target_date: Optional[str] = None
    mode: Optional[str] = None
    status: Optional[str] = None
    files_detected: Optional[int] = None
    files_imported: Optional[int] = None
    rows_imported: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class DatasetVersion(BaseModel):
    dataset_id: str
    target_date: Optional[str] = None
    market: Optional[str] = None
    source_file_hashes: Optional[object] = None
    row_counts: Optional[object] = None
    canonical_hour_mapping: Optional[bool] = None
    leakage_cutoff: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None


class DatasetReadiness(BaseModel):
    dataset_id: str
    target_date: Optional[str] = None
    status: Optional[str] = None
    row_counts: Optional[object] = None
    leakage_cutoff: Optional[str] = None
    canonical_hour_mapping: Optional[bool] = None
