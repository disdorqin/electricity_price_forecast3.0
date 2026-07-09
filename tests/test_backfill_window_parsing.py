"""Tests for CSV window parsing + per-day record building (backfill).

Validates that a GBK CSV with Chinese columns (时刻/日前电价/实时电价) is
parsed into 24-hour daily records, that 00:00 maps to hour 24, and that a
missing hour is linearly interpolated and flagged.
"""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db_ops.backfill_shandong_pmos_csv import (
    _build_day_records,
    _load_window_df,
)


def _write_gbk_csv(path: Path, date_str: str, hours=None, drop_hour=None):
    """Write a one-day CSV with Chinese columns.

    hours: list of hour ints to emit (default 1..24). Hour 24 is emitted as
    00:00 of the same calendar day (which maps to hour_business=24).
    drop_hour: hour to omit (simulate a missing row).
    """
    if hours is None:
        hours = list(range(1, 25))
    rows = []
    for h in hours:
        if h == drop_hour:
            continue
        ts = f"{date_str} 00:00" if h == 24 else f"{date_str} {h:02d}:00"
        rows.append((ts, float(h) + 100.0, float(h) + 200.0))
    df = pd.DataFrame(rows, columns=["时刻", "日前电价", "实时电价"])
    df.to_csv(path, index=False, encoding="gbk")


class TestLoadWindowDf:
    def test_parses_chinese_columns_and_window(self, tmp_path):
        p = tmp_path / "pmos.csv"
        _write_gbk_csv(p, "2026-03-01")
        df, columns, time_col, da_col, rt_col = _load_window_df(
            p, "gbk", date(2026, 3, 1), date(2026, 3, 1)
        )
        assert da_col == "日前电价"
        assert rt_col == "实时电价"
        assert time_col == "时刻"
        assert len(df) == 24

    def test_midnight_maps_to_hour_24(self, tmp_path):
        p = tmp_path / "pmos.csv"
        _write_gbk_csv(p, "2026-03-01")
        df, _, _, _, _ = _load_window_df(
            p, "gbk", date(2026, 3, 1), date(2026, 3, 1)
        )
        midnight = df[df["_ts"].dt.strftime("%H:%M") == "00:00"]
        assert len(midnight) == 1
        assert int(midnight.iloc[0]["_hb"]) == 24


class TestDayRecordsWithGap:
    def test_missing_hour_interpolated_and_flagged(self, tmp_path):
        p = tmp_path / "pmos.csv"
        _write_gbk_csv(p, "2026-03-01", drop_hour=7)
        df, _, _, da_col, rt_col = _load_window_df(
            p, "gbk", date(2026, 3, 1), date(2026, 3, 1)
        )
        da, rt, n_interp, n_missing = _build_day_records(df, da_col, rt_col)
        assert len(da) == 24 and len(rt) == 24
        # one missing hour in each of da/rt => 2 interpolated cells
        assert n_interp == 2
        # one missing row (hour 7) in the source
        assert n_missing == 1
        # interpolated hour 7 = average of neighbors (106 and 108) => 107
        assert da[6] == pytest.approx(107.0, abs=1.0)
