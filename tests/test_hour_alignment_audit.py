"""Hour-alignment audit tests (00:00 -> hour_business=24).

Pure-python (no DB). Verifies the numeric mapping AND documents the
date-ownership divergence from 2.5 (a known parity bug, MEDIUM severity).

2.5 rule (OUTPUT_CONVENTION.md): `hour 24 = D+1 00:00` belongs to business day D.
3.0 rule (backfill _hour_from_ts): keeps the calendar date, so `D 00:00` ->
(trade_date=D, hour_business=24) -- i.e. the boundary hour is tagged D, not D-1.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.db_ops.backfill_shandong_pmos_csv import _hour_from_ts  # noqa: E402


def test_hour_from_ts_numeric_mapping():
    assert _hour_from_ts(datetime(2026, 1, 1, 0, 0)) == 24   # 00:00 -> 24
    assert _hour_from_ts(datetime(2026, 1, 1, 1, 0)) == 1    # 01:00 -> 1
    assert _hour_from_ts(datetime(2026, 1, 1, 13, 0)) == 13
    assert _hour_from_ts(datetime(2026, 1, 1, 23, 0)) == 23  # 23:00 -> 23


def _business_day_3_0(ts: datetime) -> str:
    """3.0 ownership: calendar date is preserved, hb = _hour_from_ts."""
    return ts.date().isoformat(), _hour_from_ts(ts)


def _business_day_2_5(ts: datetime):
    """2.5 ownership: 00:00 belongs to the PREVIOUS business day, hb=24."""
    hb = 24 if ts.hour == 0 else ts.hour
    bd = (ts - timedelta(days=1)).date().isoformat() if ts.hour == 0 else ts.date().isoformat()
    return bd, hb


def test_3_0_keeps_calendar_date_for_midnight():
    ts = datetime(2026, 1, 2, 0, 0)
    bd, hb = _business_day_3_0(ts)
    assert (bd, hb) == ("2026-01-02", 24)
    # Same physical CSV row under 2.5 would be (2026-01-01, 24)
    bd2, hb2 = _business_day_2_5(ts)
    assert (bd2, hb2) == ("2026-01-01", 24)


def test_3_0_diverges_from_2_5_ownership_for_midnight():
    ts = datetime(2026, 1, 2, 0, 0)
    assert _business_day_3_0(ts) != _business_day_2_5(ts)


@pytest.mark.xfail(
    reason="KNOWN PARITY BUG (MEDIUM): 3.0 tags the 00:00 boundary hour with the "
           "calendar date D, while 2.5 assigns it to business day D-1 (hour 24). "
           "Must be fixed before per-day cross-system alignment / fusion with 2.5.",
    strict=False,
)
def test_should_match_2_5_ownership_for_midnight():
    ts = datetime(2026, 1, 2, 0, 0)
    assert _business_day_3_0(ts) == _business_day_2_5(ts)
