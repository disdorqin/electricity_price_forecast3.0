"""Dataset / data-source service — reads from the data-ingestion tables."""

from __future__ import annotations

from typing import List, Optional

from pymysql.connections import Connection

from .base import q_all, q_one


def list_datasets(conn: Connection, limit: int = 50) -> List[dict]:
    return q_all(
        conn,
        "SELECT * FROM efm_dataset_versions ORDER BY target_date DESC LIMIT %s",
        (int(limit),),
    )


def get_dataset(conn: Connection, dataset_id: str) -> Optional[dict]:
    return q_one(conn, "SELECT * FROM efm_dataset_versions WHERE dataset_id=%s", (dataset_id,))


def get_latest_dataset(conn: Connection, target_date: Optional[str] = None) -> Optional[dict]:
    if target_date:
        return q_one(
            conn,
            "SELECT * FROM efm_dataset_versions WHERE target_date=%s ORDER BY created_at DESC LIMIT 1",
            (target_date,),
        )
    return q_one(conn, "SELECT * FROM efm_dataset_versions ORDER BY target_date DESC LIMIT 1")


def list_data_sources(conn: Connection) -> List[dict]:
    return q_all(conn, "SELECT * FROM efm_data_sources ORDER BY source_id ASC")


def list_source_files(conn: Connection, source_id: Optional[str] = None, limit: int = 200) -> List[dict]:
    sql = (
        "SELECT file_name, file_path, file_ext, file_size, file_sha256, "
        "import_status, import_message, detected_at, imported_at "
        "FROM efm_source_files"
    )
    params: list = []
    if source_id:
        sql += " WHERE source_id=%s"
        params.append(source_id)
    sql += " ORDER BY detected_at DESC LIMIT %s"
    params.append(int(limit))
    return q_all(conn, sql, params)


def list_data_update_runs(conn: Connection, limit: int = 50) -> List[dict]:
    return q_all(
        conn,
        "SELECT update_run_id, target_date, mode, status, files_detected, files_imported, "
        "rows_imported, started_at, finished_at FROM efm_data_update_runs "
        "ORDER BY started_at DESC LIMIT %s",
        (int(limit),),
    )
