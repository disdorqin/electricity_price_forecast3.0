"""Tests for full_chain_orchestrator day-ahead DB da_anchor fallback.

Validates that ``_step_dayahead_prediction`` reads the day-ahead anchor from
the local ledger CSV first and falls back to ``efm_market_data_hourly``
(data_type='da_price') for missing hours. Uses in-memory fakes; no database.
"""
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.full_chain_orchestrator import (
    _load_da_anchor_from_market_hourly,
    _step_dayahead_prediction,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._rows)

    def close(self):
        pass


class FakeDbMgr:
    def __init__(self, rows):
        self._rows = rows

    def get_connection(self):
        return FakeConn(self._rows)


class CapturingStore:
    def __init__(self):
        self.written: list[dict[str, Any]] = []

    def write_predictions(self, run_id, target_date, preds):
        self.written.extend(preds)
        return len(preds)

    def read_predictions(self, *a, **k):
        return []


def _da_rows(n=24, base=300.0):
    return [(h, base + float(h)) for h in range(1, n + 1)]


class TestLoadDaAnchorFromMarketHourly:
    def test_returns_24_entries(self):
        mgr = FakeDbMgr(_da_rows(24))
        res = _load_da_anchor_from_market_hourly(mgr, "2026-03-01")
        assert len(res) == 24
        assert res[1] == 301.0
        assert res[24] == 324.0

    def test_empty_on_db_error(self):
        class BoomMgr:
            def get_connection(self):
                raise RuntimeError("boom")

        assert _load_da_anchor_from_market_hourly(BoomMgr(), "2026-03-01") == {}


class TestStepDayaheadFallback:
    def test_falls_back_to_db_when_ledger_empty(self, monkeypatch):
        import pandas as pd
        monkeypatch.setattr(
            "pipelines.full_chain_orchestrator._load_ledger_dataframe",
            lambda *a, **k: pd.DataFrame(),
        )
        store = CapturingStore()
        mgr = FakeDbMgr(_da_rows(24))
        msg = _step_dayahead_prediction("r1", "2026-03-01", store, mgr)
        assert "db_market_hourly" in msg
        assert len(store.written) == 24
        for p in store.written:
            assert p["stage"] == "da_anchor"
            assert p["task"] == "dayahead"

    def test_merges_ledger_and_db(self, monkeypatch):
        import pandas as pd
        ledger_df = pd.DataFrame({
            "target_day": ["2026-03-01"] * 12,
            "hour_business": list(range(1, 13)),
            "y_pred": [float(h) for h in range(1, 13)],
            "model_name": ["ledger"] * 12,
            "model_version": ["v1"] * 12,
        })
        monkeypatch.setattr(
            "pipelines.full_chain_orchestrator._load_ledger_dataframe",
            lambda *a, **k: ledger_df,
        )
        store = CapturingStore()
        mgr = FakeDbMgr(_da_rows(24))
        _step_dayahead_prediction("r1", "2026-03-01", store, mgr)
        assert len(store.written) == 24
        ledger_hours = {p["hour_business"] for p in store.written if p["model_name"] == "ledger"}
        db_hours = {p["hour_business"] for p in store.written if p["model_name"] == "da_anchor_db"}
        assert ledger_hours == set(range(1, 13))
        assert db_hours == set(range(13, 25))

    def test_no_db_and_empty_ledger_returns_no_preds(self, monkeypatch):
        import pandas as pd
        monkeypatch.setattr(
            "pipelines.full_chain_orchestrator._load_ledger_dataframe",
            lambda *a, **k: pd.DataFrame(),
        )
        store = CapturingStore()
        msg = _step_dayahead_prediction("r1", "2026-03-01", store, None)
        assert "No DA predictions" in msg
        assert store.written == []
