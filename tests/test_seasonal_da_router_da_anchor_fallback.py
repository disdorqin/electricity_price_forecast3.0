"""Tests for the non-winter da_anchor fallback in seasonal_da_router.

When neither official_baseline (realtime) nor sgdfnet raw_model are present
in non-winter months, the router must fall back to the day-ahead anchor
(da_anchor) so the day-ahead clearing price still serves as the benchmark
forecast. These tests use a minimal in-memory store; no database required.
"""
from typing import Any

import pytest

from pipelines.seasonal_da_router import run_seasonal_da_router


class MockStore:
    """Minimal PredictionStore stub recording read/write calls."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, Any, Any], list[dict]] = {}
        self.written: dict[tuple[str, str], list[dict]] = {}

    def seed(self, run_id, target_date, preds, *, stage, task=None):
        self._data[(run_id, target_date, task, stage)] = preds

    def read_predictions(self, run_id, target_date, task=None, stage=None):
        return list(self._data.get((run_id, target_date, task, stage), []))

    def write_selected_final(self, run_id, target_date, decisions):
        self.written.setdefault((run_id, target_date), []).extend(decisions)
        return len(decisions)


def _all_hours(base, stage, model=""):
    return [
        {
            "hour_business": h,
            "pred_price": round(base + h * 0.5, 4),
            "stage": stage,
            "model_name": model,
        }
        for h in range(1, 25)
    ]


RUN_ID = "t_run"
NON_WINTER = "2026-04-15"


class TestNonWinterDaAnchorFallback:
    def test_falls_back_to_da_anchor(self):
        store = MockStore()
        store.seed(RUN_ID, NON_WINTER, _all_hours(45.0, "da_anchor"), stage="da_anchor")
        res = run_seasonal_da_router(NON_WINTER, store, RUN_ID)
        assert res["status"] == "ok"
        assert res["selected_model"] == "da_anchor"
        assert res["hours_decided"] == 24
        written = store.written[(RUN_ID, NON_WINTER)]
        assert written[0]["decision_reason"] == "non_winter_da_anchor_fallback"

    def test_da_anchor_partial_is_partial(self):
        store = MockStore()
        store.seed(RUN_ID, NON_WINTER, _all_hours(45.0, "da_anchor")[:12], stage="da_anchor")
        res = run_seasonal_da_router(NON_WINTER, store, RUN_ID)
        assert res["status"] == "partial"
        assert res["hours_decided"] == 12

    def test_no_source_returns_failed(self):
        store = MockStore()
        res = run_seasonal_da_router(NON_WINTER, store, RUN_ID)
        assert res["status"] == "failed"
        assert res["hours_decided"] == 0

    def test_official_baseline_takes_precedence(self):
        store = MockStore()
        store.seed(
            RUN_ID, NON_WINTER, _all_hours(35.0, "official_baseline"),
            stage="official_baseline", task="realtime",
        )
        store.seed(RUN_ID, NON_WINTER, _all_hours(45.0, "da_anchor"), stage="da_anchor")
        res = run_seasonal_da_router(NON_WINTER, store, RUN_ID)
        assert res["selected_model"] == "official_baseline"
        written = store.written[(RUN_ID, NON_WINTER)]
        assert written[0]["decision_reason"] == "non_winter_official_baseline"
