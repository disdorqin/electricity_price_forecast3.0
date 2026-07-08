"""
Dataset builder for EFM3.

Assembles a dataset version record by verifying data completeness,
computing leakage cutoffs (D14 = target_date - 1 day 14:00), and
recording the dataset in efm_dataset_versions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, date
from typing import Any, Optional

from pymysql.connections import Connection

from .errors import DatasetNotReadyError

logger = logging.getLogger(__name__)

# Required data types for a READY dataset
_REQUIRED_TYPES = {"da_price", "rt_price"}

# D14 cutoff: target_date - 1 day 14:00
_D14_OFFSET_DAYS = 1
_D14_HOUR = 14
_D14_MINUTE = 0


class DatasetBuilder:
    """
    Builds dataset versions by checking data completeness and recording
    the dataset manifest in efm_dataset_versions.
    """

    def __init__(self, conn: Connection):
        self._conn = conn

    # ── Public API ─────────────────────────────────────────────────

    def build_dataset(
        self,
        target_date: str,
        market: str = "shandong",
    ) -> dict:
        """
        Build (or rebuild) a dataset version entry for *target_date*.

        Steps:
        1. Check that all required data types exist for the target_date.
        2. Compute leakage_cutoff = target_date - 1 day 14:00 (D14).
        3. Generate dataset_id from target_date, market, and a short hash.
        4. Collect source file hashes and row counts.
        5. Upsert into efm_dataset_versions with status READY or PARTIAL.

        Returns::

            {
                "dataset_id": str,
                "target_date": str,
                "market": str,
                "status": "READY" | "PARTIAL" | "FAIL",
                "row_counts": dict[str, int],
                "source_file_hashes": list[str],
                "leakage_cutoff": str (ISO format) | None,
            }
        """
        # 1. Check data completeness
        types_check = self._check_required_types(target_date, market)

        # 2. Compute D14 cutoff
        leakage_cutoff_dt = self._compute_d14_cutoff(target_date)

        # 3. Generate dataset_id
        raw = f"{target_date}_{market}"
        short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8]
        dataset_id = f"ds_{target_date}_{market}_{short_hash}"

        # 4. Collect source info
        row_counts = self._fetch_row_counts(target_date, market)
        source_hashes = self._fetch_source_file_hashes(target_date, market)

        # 5. Determine status
        if not types_check["passed"]:
            status = "FAIL"
        else:
            missing_types = types_check.get("data_types_missing", [])
            if missing_types:
                status = "PARTIAL"
            else:
                # Verify 24 rows for required types
                all_full = all(
                    row_counts.get(dt, 0) >= 24
                    for dt in _REQUIRED_TYPES
                )
                status = "READY" if all_full else "PARTIAL"

        # 6. Persist
        self._upsert_dataset(
            dataset_id=dataset_id,
            target_date=target_date,
            market=market,
            status=status,
            source_file_hashes=source_hashes,
            row_counts=row_counts,
            leakage_cutoff=leakage_cutoff_dt,
        )

        logger.info(
            "Dataset %s status=%s (types=%s, rows=%s)",
            dataset_id, status,
            list(row_counts.keys()),
            sum(row_counts.values()),
        )

        return {
            "dataset_id": dataset_id,
            "target_date": target_date,
            "market": market,
            "status": status,
            "row_counts": row_counts,
            "source_file_hashes": source_hashes,
            "leakage_cutoff": (
                leakage_cutoff_dt.isoformat() if leakage_cutoff_dt else None
            ),
        }

    # ── Data checks ────────────────────────────────────────────────

    def _check_required_types(self, target_date: str, market: str) -> dict:
        """
        Return dict with:
            passed: bool
            data_types_found: list[str]
            data_types_missing: list[str]
        """
        sql = """
            SELECT DISTINCT data_type
            FROM efm_market_data_hourly
            WHERE trade_date = %s AND market = %s
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (target_date, market))
            rows = cursor.fetchall()

        found = {r[0] for r in rows}
        missing = _REQUIRED_TYPES - found

        return {
            "passed": len(missing) == 0,
            "data_types_found": sorted(found),
            "data_types_missing": sorted(missing),
        }

    # ── D14 cutoff ─────────────────────────────────────────────────

    @staticmethod
    def _compute_d14_cutoff(target_date: str) -> Optional[datetime]:
        """
        Compute the D14 cutoff datetime:

            cutoff = target_date - 1 day, at 14:00

        Example:
            target_date = "2026-03-11" → cutoff = "2026-03-10T14:00:00"
        """
        try:
            td = datetime.strptime(target_date, "%Y-%m-%d")
            cutoff = td - timedelta(days=_D14_OFFSET_DAYS)
            cutoff = cutoff.replace(hour=_D14_HOUR, minute=_D14_MINUTE, second=0, microsecond=0)
            return cutoff
        except ValueError:
            logger.warning("Unable to parse target_date '%s'", target_date)
            return None

    # ── DB queries ─────────────────────────────────────────────────

    def _fetch_row_counts(self, target_date: str, market: str) -> dict[str, int]:
        sql = """
            SELECT data_type, COUNT(*) AS cnt
            FROM efm_market_data_hourly
            WHERE trade_date = %s AND market = %s
            GROUP BY data_type
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (target_date, market))
            rows = cursor.fetchall()
        return {r[0]: r[1] for r in rows}

    def _fetch_source_file_hashes(self, target_date: str, market: str) -> list[str]:
        sql = """
            SELECT DISTINCT f.file_sha256
            FROM efm_market_data_hourly h
            JOIN efm_source_files f ON h.source_file_id = f.id
            WHERE h.trade_date = %s
              AND h.market = %s
              AND f.file_sha256 IS NOT NULL
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (target_date, market))
            rows = cursor.fetchall()
        return sorted({r[0] for r in rows})

    def _upsert_dataset(
        self,
        dataset_id: str,
        target_date: str,
        market: str,
        status: str,
        source_file_hashes: list[str],
        row_counts: dict[str, int],
        leakage_cutoff: Optional[datetime],
    ) -> None:
        sql = """
            INSERT INTO efm_dataset_versions
                (dataset_id, target_date, market, source_file_hashes,
                 row_counts, canonical_hour_mapping, leakage_cutoff, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source_file_hashes    = VALUES(source_file_hashes),
                row_counts            = VALUES(row_counts),
                canonical_hour_mapping = VALUES(canonical_hour_mapping),
                leakage_cutoff        = VALUES(leakage_cutoff),
                status                = VALUES(status)
        """
        hashes_json = json.dumps(source_file_hashes)
        counts_json = json.dumps(row_counts)
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (
                dataset_id, target_date, market,
                hashes_json, counts_json,
                True,  # canonical_hour_mapping
                leakage_cutoff,
                status,
            ))
        self._conn.commit()
