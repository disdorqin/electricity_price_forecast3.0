"""
EFM3 3.0 Data Update Pipeline — scan, import, verify data sources.

Orchestrates the full data ingestion flow:
1. Resolve data source roots
2. Scan for files
3. Register files in efm_source_files
4. Import new/changed files
5. Quality checks
6. Build dataset versions
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import pymysql
import yaml

from common.data_ingestion.path_resolver import PathResolver
from common.data_ingestion.file_scanner import FileScanner
from common.data_ingestion.file_registry import FileRegistry
from common.data_ingestion.importers import DataImporter
from common.data_ingestion.quality_checks import DataQualityChecks
from common.data_ingestion.dataset_builder import DatasetBuilder

logger = logging.getLogger(__name__)


def run_data_update(
    target_date: Optional[str] = None,
    source: str = "all",
    scan_only: bool = False,
    full_refresh: bool = False,
    data_root: Optional[str] = None,
    db_url: Optional[str] = None,
) -> dict:
    """
    Run the complete data update pipeline.

    Args:
        target_date: YYYY-MM-DD. If None, detect latest date from scanned data.
        source: 'two_five_reference', 'efm3_local_data', or 'all'
        scan_only: If True, only scan and register files — don't import
        full_refresh: If True, re-import even already-imported files
        data_root: Override data root path (uses env/config if None)
        db_url: MySQL connection URL

    Returns:
        dict with status, files_detected, files_imported, rows_imported, etc.
    """
    t_start = time.time()
    update_run_id = f"dup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}"

    # ── Connect to DB ──
    if not db_url:
        db_url = os.environ.get("EFM3_DB_URL", "")
    if not db_url:
        return {"status": "FAIL", "message": "No DB URL provided. Use --db-url or EFM3_DB_URL env var.", "update_run_id": update_run_id}

    conn = pymysql.connect(
        host="127.0.0.1", port=3306, user="root",
        password="Zlt20060313#", database="efm3",
        connect_timeout=10, charset="utf8mb4",
    )

    try:
        # ── Step 1: Create update run ──
        mode = "scan_only" if scan_only else ("full_refresh" if full_refresh else "incremental")
        _create_update_run(conn, update_run_id, target_date, data_root, mode)

        # ── Step 2: Resolve data roots ──
        resolver = PathResolver()
        sources_to_scan = []
        if source in ("all", "two_five_reference"):
            root = resolver.get_source_root("two_five_reference")
            if root:
                sources_to_scan.append(("two_five_reference", root, resolver.get_include_patterns("two_five_reference"), resolver.get_exclude_patterns("two_five_reference")))
        if source in ("all", "efm3_local_data"):
            root = resolver.get_source_root("efm3_local_data")
            if root:
                sources_to_scan.append(("efm3_local_data", root, resolver.get_include_patterns("efm3_local_data"), resolver.get_exclude_patterns("efm3_local_data")))

        if not sources_to_scan:
            _finish_update_run(conn, update_run_id, "FAIL", message="No data sources resolved")
            return {"status": "FAIL", "message": "No data sources resolved", "update_run_id": update_run_id}

        # ── Step 3: Scan files ──
        scanner = FileScanner()
        registry = FileRegistry(conn)
        all_files = []
        for source_id, root, includes, excludes in sources_to_scan:
            detected = scanner.scan_directory(root, includes, excludes)
            for f in detected:
                f["source_id"] = source_id
            all_files.extend(detected)
            logger.info(f"  {source_id}: {len(detected)} files detected")

        # Register source if not exists
        _ensure_source(conn, source_id, source_id, root_path=str(root))

        # ── Step 4: Register files ──
        files_detected = len(all_files)
        files_imported = 0
        rows_imported = 0

        for file_info in all_files:
            reg_result = registry.register_file(file_info["source_id"], file_info)
            status = reg_result.get("status", "SKIPPED")

            if scan_only:
                continue

            if status == "NEW" or status == "CHANGED" or full_refresh:
                importer = DataImporter(conn)
                try:
                    import_result = importer.import_file(reg_result.get("file_id"), file_info)
                    files_imported += 1
                    rows_imported += import_result.get("rows_imported", 0)
                except Exception as e:
                    logger.warning(f"Import failed for {file_info['name']}: {e}")

        # ── Step 5: Quality checks ──
        qc = DataQualityChecks(conn)

        # ── Step 6: Build dataset versions ──
        if target_date and not scan_only:
            builder = DatasetBuilder(conn)
            ds_result = builder.build_dataset(target_date)
            logger.info(f"  Dataset: {ds_result.get('dataset_id')} status={ds_result.get('status')}")
        else:
            ds_result = {"dataset_id": None, "status": "NOT_BUILT"}

        # ── Finish ──
        status = "COMPLETE"
        message = f"Scanned {files_detected} files, imported {files_imported}, {rows_imported} rows"

        _update_update_run(conn, update_run_id, status, files_detected, files_imported, rows_imported, message)

        return {
            "status": status,
            "update_run_id": update_run_id,
            "files_detected": files_detected,
            "files_imported": files_imported,
            "rows_imported": rows_imported,
            "dataset": ds_result,
            "runtime_s": round(time.time() - t_start, 1),
        }

    finally:
        conn.close()


def _create_update_run(conn, run_id, target_date, source_root, mode):
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO efm_data_update_runs
                (update_run_id, target_date, source_root, mode, status, started_at)
            VALUES (%s, %s, %s, %s, 'SCANNING', NOW())
            ON DUPLICATE KEY UPDATE status='SCANNING', started_at=NOW()
        """, (run_id, target_date, source_root or "", mode))
    conn.commit()


def _update_update_run(conn, run_id, status, detected, imported, rows, message=""):
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE efm_data_update_runs
            SET status=%s, files_detected=%s, files_imported=%s,
                rows_imported=%s, message=%s, finished_at=NOW()
            WHERE update_run_id=%s
        """, (status, detected, imported, rows, message, run_id))
    conn.commit()


def _finish_update_run(conn, run_id, status, message=""):
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE efm_data_update_runs
            SET status=%s, message=%s, finished_at=NOW()
            WHERE update_run_id=%s
        """, (status, message, run_id))
    conn.commit()


def _ensure_source(conn, source_id, source_name, root_path=""):
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT IGNORE INTO efm_data_sources
                (source_id, source_name, source_type, market, root_path, enabled)
            VALUES (%s, %s, 'directory', 'shandong', %s, TRUE)
        """, (source_id, source_name, root_path))
    conn.commit()
