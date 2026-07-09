"""Unit tests for tools.db_ops.backfill_shandong_pmos_csv pure helpers.

Covers DB URL parsing (with %23 password decode), encoding detection,
column candidate matching, hour canonical mapping, and missing-value
linear interpolation. No database or network access required.
"""
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db_ops.backfill_shandong_pmos_csv import (
    _build_day_records,
    _detect_encoding,
    _hour_from_ts,
    _interpolate_day,
    _pick_column,
    parse_db_url,
)


class TestParseDbUrl:
    def test_decodes_percent_23_password(self):
        host, port, user, pw, database = parse_db_url(
            "mysql+pymysql://root:Zlt20060313%23@127.0.0.1:3306/efm3"
        )
        assert user == "root"
        assert pw == "Zlt20060313#"
        assert host == "127.0.0.1"
        assert port == 3306
        assert database == "efm3"

    def test_plain_password(self):
        _, _, user, pw, _ = parse_db_url("mysql://u:p@localhost:3306/db")
        assert (user, pw) == ("u", "p")


class TestDetectEncoding:
    def test_detects_gbk(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_bytes("时刻,日前电价\n".encode("gbk"))
        assert _detect_encoding(p) == "gbk"

    def test_detects_utf8(self, tmp_path):
        # Use a char (é) that is valid UTF-8 but NOT decodable as GBK, so the
        # sniffer falls through gbk/gb18030 to utf-8.
        p = tmp_path / "x.csv"
        p.write_text("café,test\n", encoding="utf-8")
        assert _detect_encoding(p) == "utf-8"


class TestPickColumn:
    def test_picks_chinese(self):
        assert _pick_column(["时刻", "日前电价", "实时电价"], ["日前电价"]) == "日前电价"

    def test_returns_none_when_missing(self):
        assert _pick_column(["a", "b"], ["日前电价", "da_price"]) is None

    def test_picks_english(self):
        assert _pick_column(["ds", "da_price"], ["da_price"]) == "da_price"


class TestHourFromTs:
    def test_midnight_to_24(self):
        assert _hour_from_ts(datetime(2026, 1, 1, 0, 0)) == 24

    def test_one_am_to_1(self):
        assert _hour_from_ts(datetime(2026, 1, 1, 1, 0)) == 1

    def test_23_to_23(self):
        assert _hour_from_ts(datetime(2026, 1, 1, 23, 0)) == 23


class TestInterpolateDay:
    def test_no_missing(self):
        vals, n = _interpolate_day([float(i) for i in range(24)])
        assert n == 0 and len(vals) == 24

    def test_single_gap(self):
        raw = [float(i) for i in range(24)]
        raw[5] = None
        vals, n = _interpolate_day(raw)
        assert n == 1 and vals[5] == 5.0

    def test_all_missing(self):
        vals, n = _interpolate_day([None] * 24)
        assert n == 24 and all(v == 0.0 for v in vals)


class TestBuildDayRecords:
    def test_full_day(self):
        import pandas as pd
        df_day = pd.DataFrame({
            "_hb": list(range(1, 25)),
            "da": [float(i) for i in range(1, 25)],
            "rt": [float(i) * 2 for i in range(1, 25)],
        })
        da, rt, n_interp, n_missing = _build_day_records(df_day, "da", "rt")
        assert len(da) == 24 and len(rt) == 24
        assert n_interp == 0 and n_missing == 0
        assert da[0] == 1.0 and rt[23] == 48.0
