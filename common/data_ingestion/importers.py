"""
File importers for EFM3 — reads .xlsx, .csv, .parquet files and
writes parsed data into efm_market_data_hourly and efm_actual_prices.

Canonical hour mapping:
    00:00 → hour_business = 24
    01:00 → hour_business = 1
    ...
    23:00 → hour_business = 23

Leakage notice: actual columns are imported but MUST NOT be used as
features for the same target date. They are stored for post-hoc
evaluation / backtesting only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
from pymysql.connections import Connection

from .errors import ImportError

logger = logging.getLogger(__name__)

# ── 2.5 source column → efm data_type mapping ─────────────────────

COLUMN_TO_DATA_TYPE: dict[str, str] = {
    # Prices (2)
    "日前电价": "da_price",
    "实时电价": "rt_price",
    # Forecast / schedule columns (10)
    "地方电厂总加预测值": "fcast_local_plant",
    "联络线受电负荷预测值": "fcast_interconnect",
    "风电总加预测值": "fcast_wind",
    "光伏总加预测值": "fcast_solar",
    "核电总加预测值": "fcast_nuclear",
    "自备机组总加预测值": "fcast_self_unit",
    "试验机组总加预测值": "fcast_test_unit",
    "直调负荷预测值": "fcast_load",
    "竞价空间预测值": "fcast_bidding_space",
    "新能源总加预测值": "fcast_renewable",
    # Actual / measured columns (10)
    "地方电厂总加实际值": "actual_local_plant",
    "联络线受电负荷实际值": "actual_interconnect",
    "风电总加实际值": "actual_wind",
    "光伏总加实际值": "actual_solar",
    "核电总加实际值": "actual_nuclear",
    "自备机组总加实际值": "actual_self_unit",
    "试验机组总加实际值": "actual_test_unit",
    "直调负荷实际值": "actual_load",
    "竞价空间实际值": "actual_bidding_space",
    "新能源总加实际值": "actual_renewable",
}

TIME_COLUMN = "时刻"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


class DataImporter:
    """
    Reads data files and imports them into EFM3 database tables.
    """

    def __init__(self, conn: Connection, market: str = "shandong"):
        self._conn = conn
        self._market = market

    # ── Public API ─────────────────────────────────────────────────

    def import_file(self, file_id: int, file_info: dict) -> dict:
        """
        Import a single file into DB tables.

        *file_info* must contain keys: ``path``, ``ext``.

        Returns dict::

            {
                "file_id": int,
                "rows_imported": int,
                "data_types": list[str],
                "error": str | None,
            }
        """
        path = file_info.get("path", "")
        ext = file_info.get("ext", "").lower()

        try:
            df = self._read_file(path, ext)
        except Exception as exc:
            raise ImportError(f"Failed to read '{path}': {exc}") from exc

        if df.empty:
            return {"file_id": file_id, "rows_imported": 0, "data_types": [], "error": None}

        # Parse timestamps
        if TIME_COLUMN not in df.columns:
            raise ImportError(f"Missing time column '{TIME_COLUMN}' in {path}")
        df["_ts"] = pd.to_datetime(df[TIME_COLUMN], errors="coerce")
        df = df.dropna(subset=["_ts"]).sort_values("_ts").reset_index(drop=True)

        # Determine which recognised data-type columns exist
        available_cols = [
            c for c in COLUMN_TO_DATA_TYPE if c in df.columns
        ]
        data_types_found = [COLUMN_TO_DATA_TYPE[c] for c in available_cols]

        # Insert into efm_market_data_hourly
        rows_inserted = 0
        for _, row in df.iterrows():
            ts: datetime = row["_ts"].to_pydatetime()
            trade_date = ts.date()
            hour_business = ts.hour
            if hour_business == 0:
                hour_business = 24

            for source_col in available_cols:
                data_type = COLUMN_TO_DATA_TYPE[source_col]
                value = row[source_col]
                if pd.isna(value):
                    continue
                try:
                    val_float = float(value)
                except (ValueError, TypeError):
                    continue

                self._upsert_hourly(data_type, trade_date, hour_business, val_float, file_id)
                rows_inserted += 1

        # Update efm_actual_prices with DA anchor and RT actual
        self._upsert_actual_prices(df, file_id, path)

        logger.info(
            "Imported %d rows (file_id=%d, types=%s)",
            rows_inserted, file_id, data_types_found,
        )
        self._conn.commit()

        return {
            "file_id": file_id,
            "rows_imported": rows_inserted,
            "data_types": data_types_found,
            "error": None,
        }

    # ── File readers ───────────────────────────────────────────────

    @staticmethod
    def _read_file(path: str, ext: str) -> pd.DataFrame:
        if ext == ".xlsx":
            return pd.read_excel(path, engine="openpyxl")
        elif ext == ".xls":
            return pd.read_excel(path, engine="xlrd")
        elif ext == ".csv":
            try:
                return pd.read_csv(path, encoding="gbk")
            except (UnicodeDecodeError, UnicodeError):
                return pd.read_csv(path, encoding="utf-8")
        elif ext == ".parquet":
            return pd.read_parquet(path)
        else:
            raise ImportError(f"Unsupported file extension: '{ext}'")

    # ── DB writes ──────────────────────────────────────────────────

    def _upsert_hourly(
        self,
        data_type: str,
        trade_date: Any,  # date-like
        hour_business: int,
        value: float,
        file_id: int,
    ) -> None:
        sql = """
            INSERT INTO efm_market_data_hourly
                (market, data_type, trade_date, hour_business, value, source_file_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value          = VALUES(value),
                source_file_id = VALUES(source_file_id)
        """
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (
                self._market, data_type, trade_date, hour_business,
                value, file_id,
            ))

    def _upsert_actual_prices(self, df: pd.DataFrame, file_id: int, file_path: str) -> None:
        """Populate efm_actual_prices from DA and RT columns."""
        da_col = "日前电价"
        rt_col = "实时电价"
        has_da = da_col in df.columns
        has_rt = rt_col in df.columns
        if not has_da and not has_rt:
            return

        sql = """
            INSERT INTO efm_actual_prices
                (target_date, hour_business, da_anchor, rt_actual, source_file)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                da_anchor   = COALESCE(VALUES(da_anchor), da_anchor),
                rt_actual   = COALESCE(VALUES(rt_actual), rt_actual),
                source_file = VALUES(source_file)
        """
        for _, row in df.iterrows():
            ts: datetime = row["_ts"].to_pydatetime()
            trade_date = ts.date()
            hb = ts.hour
            if hb == 0:
                hb = 24

            da_val = None if not has_da else (
                None if pd.isna(row[da_col]) else float(row[da_col])
            )
            rt_val = None if not has_rt else (
                None if pd.isna(row[rt_col]) else float(row[rt_col])
            )
            if da_val is None and rt_val is None:
                continue

            with self._conn.cursor() as cursor:
                cursor.execute(sql, (
                    trade_date, hb, da_val, rt_val, file_path,
                ))
