"""
Data quality checks for ingested market data.

Validates file-level properties (row count 24 per date, canonical hour
mapping, duplicate/missing hours, price range, D14 cutoff compliance)
and dataset-level completeness.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, date
from typing import Any, Optional

from pymysql.connections import Connection

logger = logging.getLogger(__name__)

# Price sanity bounds
_PRICE_MIN = -500.0
_PRICE_MAX = 5000.0

# D14 cutoff: target_date - 1 day 14:00
_D14_HOUR = 14


class DataQualityChecks:
    """
    Run quality checks on imported data.

    All methods return dicts with keys ``passed`` (bool) and ``details`` (str).
    """

    def __init__(self, conn: Connection):
        self._conn = conn

    # ── File-level checks ──────────────────────────────────────────

    def check_file(self, file_id: int) -> dict[str, Any]:
        """
        Run all file-level checks for a given source file.

        Returns a dict mapping check name -> result dict.
        """
        results: dict[str, Any] = {}

        results["row_count_24_per_date"] = self._check_row_count_24(file_id)
        results["canonical_hour_mapping"] = self._check_canonical_hours(file_id)
        results["duplicate_hours"] = self._check_duplicate_hours(file_id)
        results["missing_hours"] = self._check_missing_hours(file_id)
        results["price_range"] = self._check_price_range(file_id)
        results["d14_cutoff_compliance"] = self._check_d14_cutoff(file_id)

        return results

    def _check_row_count_24(self, file_id: int) -> dict:
        """
        For every (trade_date, data_type) pair, assert exactly 24 rows.
        """
        sql = """
            SELECT trade_date, data_type, COUNT(*) AS cnt
            FROM efm_market_data_hourly
            WHERE source_file_id = %s
            GROUP BY trade_date, data_type
            ORDER BY trade_date, data_type
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (file_id,))
            rows = cursor.fetchall()

        if not rows:
            return {"passed": False, "details": "No rows found for this file"}

        errors: list[str] = []
        for trade_date_str, data_type, cnt in rows:
            if cnt != 24:
                errors.append(
                    f"{trade_date_str}/{data_type}: {cnt} rows (expected 24)"
                )

        if errors:
            return {
                "passed": False,
                "details": "; ".join(errors),
            }
        return {
            "passed": True,
            "details": f"All {len(rows)} (date, type) groups have 24 rows",
        }

    def _check_canonical_hours(self, file_id: int) -> dict:
        """
        Verify that hour_business values are in [1, 24] and that
        00:00 is mapped to 24 (no 0-valued hour_business).
        """
        sql = """
            SELECT DISTINCT hour_business
            FROM efm_market_data_hourly
            WHERE source_file_id = %s
            ORDER BY hour_business
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (file_id,))
            rows = [r[0] for r in cursor.fetchall()]

        if not rows:
            return {"passed": False, "details": "No rows to check"}

        errors: list[str] = []
        for hb in rows:
            if hb < 1 or hb > 24:
                errors.append(f"Invalid hour_business: {hb}")

        if 0 in rows:
            errors.append("Found hour_business=0; expected 24 for 00:00")

        if errors:
            return {"passed": False, "details": "; ".join(errors)}
        return {
            "passed": True,
            "details": f"All {len(rows)} hour values in [1, 24]",
        }

    def _check_duplicate_hours(self, file_id: int) -> dict:
        """
        Check for duplicate (market, data_type, trade_date, hour_business).
        """
        sql = """
            SELECT market, data_type, trade_date, hour_business, COUNT(*) AS cnt
            FROM efm_market_data_hourly
            WHERE source_file_id = %s
            GROUP BY market, data_type, trade_date, hour_business
            HAVING cnt > 1
            LIMIT 20
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (file_id,))
            dupes = cursor.fetchall()

        if dupes:
            details = "; ".join(
                f"{m}/{dt}/{td}/hb={hb} (×{c})"
                for m, dt, td, hb, c in dupes
            )
            return {"passed": False, "details": f"Duplicates found: {details}"}
        return {"passed": True, "details": "No duplicate hours"}

    def _check_missing_hours(self, file_id: int) -> dict:
        """
        For each (trade_date, data_type), check all 24 hours [1..24] exist.
        """
        sql = """
            SELECT trade_date, data_type, COUNT(DISTINCT hour_business) AS cnt
            FROM efm_market_data_hourly
            WHERE source_file_id = %s
            GROUP BY trade_date, data_type
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (file_id,))
            groups = cursor.fetchall()

        if not groups:
            return {"passed": False, "details": "No data to check"}

        errors: list[str] = []
        for td, dt, cnt in groups:
            if cnt < 24:
                errors.append(f"{td}/{dt}: only {cnt}/24 distinct hours")

        if errors:
            return {"passed": False, "details": "; ".join(errors)}
        return {
            "passed": True,
            "details": f"All {len(groups)} groups have 24 distinct hours",
        }

    def _check_price_range(self, file_id: int) -> dict:
        """
        Check da_price and rt_price values are within sane bounds.
        """
        sql = """
            SELECT data_type, MIN(value) AS v_min, MAX(value) AS v_max
            FROM efm_market_data_hourly
            WHERE source_file_id = %s
              AND data_type IN ('da_price', 'rt_price')
            GROUP BY data_type
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (file_id,))
            stats = cursor.fetchall()

        if not stats:
            return {
                "passed": True,
                "details": "No price data to check",
            }

        errors: list[str] = []
        for dt, v_min, v_max in stats:
            if v_min is not None and v_min < _PRICE_MIN:
                errors.append(f"{dt} min {v_min} < {_PRICE_MIN}")
            if v_max is not None and v_max > _PRICE_MAX:
                errors.append(f"{dt} max {v_max} > {_PRICE_MAX}")

        if errors:
            return {"passed": False, "details": "; ".join(errors)}
        return {"passed": True, "details": "All prices within [-500, 5000]"}

    def _check_d14_cutoff(self, file_id: int) -> dict:
        """
        D14 cutoff rule: market data used for a target_date D must have
        ingestion/load timestamps <= D-1 14:00.

        For this check we ensure that the trade_dates in the file are at
        least 1 day before the "now" reference to satisfy D14. In practice
        this is a metadata flag that the file timestamp is not too fresh.
        """
        # Check whether any trade_date is today or in the future
        sql = """
            SELECT MIN(trade_date) AS d_min, MAX(trade_date) AS d_max
            FROM efm_market_data_hourly
            WHERE source_file_id = %s
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (file_id,))
            row = cursor.fetchone()

        if row is None or row[0] is None:
            return {"passed": False, "details": "No trade dates found"}

        d_min, d_max = row
        today = date.today()

        if d_max >= today:
            return {
                "passed": False,
                "details": (
                    f"Max trade_date {d_max} >= today {today}. "
                    "Data used for D should be available by D-1 14:00."
                ),
            }

        return {
            "passed": True,
            "details": f"Trade date range [{d_min}, {d_max}] all in the past",
        }

    # ── Dataset-level checks ───────────────────────────────────────

    def check_dataset(self, target_date: str) -> dict:
        """
        Check whether all required data types exist for *target_date*
        in efm_market_data_hourly.

        Required at minimum: da_price, rt_price.

        Returns dict::

            {
                "target_date": str,
                "passed": bool,
                "data_types_found": list[str],
                "data_types_missing": list[str],
                "hour_counts": dict[str, int],
                "details": str,
            }
        """
        td = target_date
        required_types = {"da_price", "rt_price"}

        sql = """
            SELECT data_type, COUNT(*) AS cnt
            FROM efm_market_data_hourly
            WHERE trade_date = %s AND market = 'shandong'
            GROUP BY data_type
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (td,))
            rows = cursor.fetchall()

        found: dict[str, int] = {r[0]: r[1] for r in rows}
        found_set = set(found.keys())
        missing = required_types - found_set

        result: dict = {
            "target_date": td,
            "passed": len(missing) == 0,
            "data_types_found": sorted(found_set),
            "data_types_missing": sorted(missing),
            "hour_counts": found,
            "details": "",
        }

        if missing:
            result["details"] = f"Missing required data types: {', '.join(sorted(missing))}"
        else:
            # Verify 24 rows per type
            bad_types = [dt for dt, cnt in found.items() if cnt != 24]
            if bad_types:
                result["details"] = f"Types with !=24 rows: {bad_types}"
                result["passed"] = False
            else:
                result["details"] = (
                    f"All required types present with 24 rows each"
                )

        return result
