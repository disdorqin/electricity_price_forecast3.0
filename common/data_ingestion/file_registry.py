"""
File registration against the efm_source_files table.

Determines whether a scanned file is NEW, SKIPPED (same hash),
CHANGED (different hash), or FAILED (registration error) and upserts
the record into the database.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from pymysql.connections import Connection

logger = logging.getLogger(__name__)


class FileRegistry:
    """
    Registers scanned file metadata into the efm_source_files table
    and reports the import status.
    """

    _STATUS_NEW = "NEW"
    _STATUS_IMPORTED = "IMPORTED"
    _STATUS_SKIPPED = "SKIPPED"
    _STATUS_CHANGED = "CHANGED"
    _STATUS_FAILED = "FAILED"

    def __init__(self, conn: Connection):
        self._conn = conn

    # ── Public API ─────────────────────────────────────────────────

    def register_file(self, source_id: str, file_info: dict) -> dict:
        """
        Register (upsert) a scanned file in efm_source_files.

        *file_info* is expected to have keys: ``path``, ``name``, ``ext``,
        ``size_bytes``, ``mtime``, ``sha256``.

        Returns a dict with keys:
            file_id  – the DB primary key of the row
            status   – NEW / SKIPPED / CHANGED / FAILED
            message  – human-readable explanation
        """
        name = file_info.get("name", "")
        sha256 = file_info.get("sha256", "")
        file_path = file_info.get("path", "")
        file_ext = file_info.get("ext", "")
        file_size = file_info.get("size_bytes")
        file_mtime = file_info.get("mtime")

        try:
            existing = self._get_existing(source_id, sha256, name)
        except Exception as exc:
            logger.error("Failed to query existing file: %s", exc)
            return {"file_id": None, "status": self._STATUS_FAILED, "message": str(exc)}

        status = self._determine_status(existing, sha256, name)

        try:
            file_id = self._upsert(source_id, file_path, name, file_ext,
                                   file_size, file_mtime, sha256, status)
        except Exception as exc:
            logger.error("Failed to upsert file record: %s", exc)
            return {"file_id": None, "status": self._STATUS_FAILED, "message": str(exc)}

        message = f"File '{name}' → {status}"
        logger.info("Registered file %s (source=%s): %s", name, source_id, status)
        return {"file_id": file_id, "status": status, "message": message}

    def get_file_id(self, source_id: str, sha256: str, name: str) -> Optional[int]:
        """
        Return the primary key for an existing file record, or None.
        """
        row = self._get_existing(source_id, sha256, name)
        if row:
            return row["id"]
        return None

    # ── Status logic ───────────────────────────────────────────────

    @staticmethod
    def _determine_status(existing: Optional[dict], sha256: str, name: str) -> str:
        """
        | Condition                                          | Status    |
        |----------------------------------------------------|-----------|
        | Not in DB at all                                   | NEW       |
        | Same sha256 + same name (no change)                | SKIPPED   |
        | Same name + different sha256 (content changed)     | CHANGED   |
        """
        if existing is None:
            return FileRegistry._STATUS_NEW

        existing_sha = existing.get("file_sha256") or ""
        if existing_sha == sha256:
            return FileRegistry._STATUS_SKIPPED

        return FileRegistry._STATUS_CHANGED

    # ── Database operations ───────────────────────────────────────

    def _get_existing(self, source_id: str, sha256: str, name: str) -> Optional[dict]:
        sql = """
            SELECT id, source_id, file_sha256, file_name, import_status
            FROM efm_source_files
            WHERE source_id = %s AND file_sha256 = %s AND file_name = %s
            LIMIT 1
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (source_id, sha256, name))
            row = cursor.fetchone()
        if row is None:
            return None
        cols = ["id", "source_id", "file_sha256", "file_name", "import_status"]
        return dict(zip(cols, row))

    def _upsert(
        self,
        source_id: str,
        file_path: str,
        file_name: str,
        file_ext: str,
        file_size: Optional[int],
        file_mtime: Optional[str],
        file_sha256: str,
        status: str,
    ) -> int:
        sql = """
            INSERT INTO efm_source_files
                (source_id, file_path, file_name, file_ext, file_size,
                 file_mtime, file_sha256, import_status, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                file_path        = VALUES(file_path),
                file_ext         = VALUES(file_ext),
                file_size        = VALUES(file_size),
                file_mtime       = VALUES(file_mtime),
                file_sha256      = VALUES(file_sha256),
                import_status    = VALUES(import_status),
                metadata_json    = VALUES(metadata_json)
        """
        metadata = json.dumps({"registered_via": "file_scanner"})

        with self._conn.cursor() as cursor:
            cursor.execute(sql, (
                source_id, file_path, file_name, file_ext,
                file_size, file_mtime, file_sha256, status, metadata,
            ))
            file_id = cursor.lastrowid
        self._conn.commit()
        return file_id
