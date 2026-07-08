"""
Tests for pipelines.seasonal_da_router — Seasonal DA Policy Router.

Validates season-based switching between da_anchor (winter) and
official_baseline (non-winter), including fallback to sgdfnet.
"""

from __future__ import annotations

from typing import Any

import pytest

from pipelines.seasonal_da_router import (
    POLICY_NAME,
    _build_hour_map,
    _is_winter,
    run_seasonal_da_router,
)

# ---------------------------------------------------------------------------
# SimpleMockStore — lightweight in-memory stub for PredictionStore
# ---------------------------------------------------------------------------


class SimpleMockStore:
    """In-memory mock that behaves like PredictionStore for testing purposes.

    Stores written predictions in a dict so callers can introspect what
    was persisted.
    """

    def __init__(self) -> None:
        # Key: (run_id, target_date, task, stage) -> list[dict]
        self._data: dict[tuple[str, str, str | None, str | None], list[dict]] = {}
        # Key: (run_id, target_date) -> list[dict]  (accumulated decisions)
        self.written_decisions: dict[tuple[str, str], list[dict]] = {}

    # ── seeding helpers (used in tests to set up mock data) ──────────

    def seed_predictions(
        self,
        run_id: str,
        target_date: str,
        predictions: list[dict[str, Any]],
        task: str | None = None,
        stage: str | None = None,
    ) -> None:
        key = (run_id, target_date, task, stage)
        self._data[key] = predictions

    def add_hour(
        self,
        run_id: str,
        target_date: str,
        hour_business: int,
        pred_price: float,
        *,
        stage: str = "da_anchor",
        task: str | None = None,
        model_name: str = "",
    ) -> None:
        """Convenience: add a single hour prediction for the given stage."""
        key = (run_id, target_date, task, stage)
        self._data.setdefault(key, []).append(
            {
                "hour_business": hour_business,
                "pred_price": pred_price,
                "stage": stage,
                "model_name": model_name,
            }
        )

    # ── PredictionStore interface ───────────────────────────────────

    def read_predictions(
        self,
        run_id: str,
        target_date: str,
        task: str | None = None,
        stage: str | None = None,
    ) -> list[dict[str, Any]]:
        key = (run_id, target_date, task, stage)
        # Return a *copy* so test assertions are not affected by mutation
        return list(self._data.get(key, []))

    def write_selected_final(
        self,
        run_id: str,
        target_date: str,
        decisions: list[dict[str, Any]],
    ) -> int:
        key = (run_id, target_date)
        self.written_decisions.setdefault(key, []).extend(decisions)
        return len(decisions)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RUN_ID = "test_run_001"
WINTER_DATE = "2026-01-15"  # January → winter
NON_WINTER_DATE = "2026-06-10"  # June → non-winter


@pytest.fixture
def store() -> SimpleMockStore:
    return SimpleMockStore()


def _build_all_hours(
    base_price: float = 50.0,
    *,
    stage: str = "da_anchor",
    model_name: str = "",
) -> list[dict[str, Any]]:
    """Build a list of 24 predictions (hour_business 1..24)."""
    return [
        {
            "hour_business": hb,
            "pred_price": round(base_price + hb * 0.5, 4),
            "stage": stage,
            "model_name": model_name,
        }
        for hb in range(1, 25)
    ]


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestIsWinter:
    def test_winter_january(self) -> None:
        assert _is_winter("2026-01-15") is True

    def test_winter_february(self) -> None:
        assert _is_winter("2026-02-01") is True

    def test_winter_november(self) -> None:
        assert _is_winter("2025-11-01") is True

    def test_winter_december(self) -> None:
        assert _is_winter("2025-12-25") is True

    def test_non_winter_march(self) -> None:
        assert _is_winter("2026-03-01") is False

    def test_non_winter_summer(self) -> None:
        assert _is_winter("2026-07-15") is False

    def test_non_winter_october(self) -> None:
        assert _is_winter("2026-10-31") is False


class TestBuildHourMap:
    def test_empty_list(self) -> None:
        assert _build_hour_map([]) == {}

    def test_maps_by_hour_business(self) -> None:
        preds = [
            {"hour_business": 1, "pred_price": 10.0},
            {"hour_business": 24, "pred_price": 99.0},
        ]
        result = _build_hour_map(preds)
        assert result[1] == {"hour_business": 1, "pred_price": 10.0}
        assert result[24] == {"hour_business": 24, "pred_price": 99.0}

    def test_hour_business_is_int(self) -> None:
        preds = [
            {"hour_business": "5", "pred_price": 30.0},
            {"hour_business": "12", "pred_price": 40.0},
        ]
        result = _build_hour_map(preds)
        assert 5 in result
        assert 12 in result
        assert isinstance(list(result.keys())[0], int)


# ---------------------------------------------------------------------------
# Integration tests for run_seasonal_da_router
# ---------------------------------------------------------------------------


class TestRunSeasonalDARouter:

    # ── import / callable smoke test ──────────────────────────────

    def test_module_importable(self) -> None:
        """Requirement 1: seasonal_da_router module can be imported."""
        from pipelines import seasonal_da_router  # noqa: F811

        assert seasonal_da_router is not None

    def test_run_seasonal_da_router_exists_and_callable(self) -> None:
        """Requirement 2: run_seasonal_da_router exists and is callable."""
        assert callable(run_seasonal_da_router)

    # ── Winter path: da_anchor ────────────────────────────────────

    def test_winter_selects_da_anchor(self, store: SimpleMockStore) -> None:
        """Requirement 3a: Winter months (Jan) select da_anchor."""
        all_hours = _build_all_hours(base_price=45.0, stage="da_anchor")
        store.seed_predictions(
            RUN_ID, WINTER_DATE, all_hours,
            stage="da_anchor",
        )

        result = run_seasonal_da_router(WINTER_DATE, store, RUN_ID)

        assert result["status"] == "ok"
        assert result["policy"] == POLICY_NAME
        assert result["selected_model"] == "da_anchor"
        assert result["hours_decided"] == 24
        assert result["hours_missing"] == 0

        # Verify the decisions that were persisted
        key = (RUN_ID, WINTER_DATE)
        written = store.written_decisions[key]
        assert len(written) == 24
        for dec in written:
            assert dec["selected_model"] == "da_anchor"
            assert dec["policy_name"] == POLICY_NAME
            assert dec["decision_reason"] == "winter_da_anchor_policy"
        # All 24 distinct hours
        written_hours = {d["hour_business"] for d in written}
        assert written_hours == set(range(1, 25))

    def test_non_winter_selects_official_baseline(
        self, store: SimpleMockStore,
    ) -> None:
        """Requirement 3b: Non-winter months (June) select official_baseline."""
        all_hours = _build_all_hours(
            base_price=35.0, stage="official_baseline",
        )
        store.seed_predictions(
            RUN_ID, NON_WINTER_DATE, all_hours,
            stage="official_baseline", task="realtime",
        )

        result = run_seasonal_da_router(NON_WINTER_DATE, store, RUN_ID)

        assert result["status"] == "ok"
        assert result["selected_model"] == "official_baseline"
        assert result["hours_decided"] == 24

        written = store.written_decisions[(RUN_ID, NON_WINTER_DATE)]
        for dec in written:
            assert dec["selected_model"] == "official_baseline"
            assert dec["decision_reason"] == "non_winter_official_baseline"

    def test_non_winter_fallback_to_sgdfnet(
        self, store: SimpleMockStore,
    ) -> None:
        """Requirement 3c: When official_baseline is missing in non-winter,
        fall back to sgdfnet raw model."""
        # No official_baseline seeded — only sgdfnet raw_model rows
        all_sgdf = _build_all_hours(
            base_price=40.0, stage="raw_model", model_name="sgdfnet",
        )
        store.seed_predictions(
            RUN_ID, NON_WINTER_DATE, all_sgdf,
            stage="raw_model",
        )
        # Also seed other raw model predictions to ensure filtering works
        store.add_hour(
            RUN_ID, NON_WINTER_DATE, 1, 99.0,
            stage="raw_model", model_name="other_model",
        )

        result = run_seasonal_da_router(NON_WINTER_DATE, store, RUN_ID)

        assert result["status"] == "ok"
        assert result["selected_model"] == "sgdfnet"
        assert result["hours_decided"] == 24

        written = store.written_decisions[(RUN_ID, NON_WINTER_DATE)]
        for dec in written:
            assert dec["selected_model"] == "sgdfnet"
            assert (
                dec["decision_reason"]
                == "non_winter_official_baseline_fallback_sgdfnet"
            )

    def test_all_24_hours_get_decision(
        self, store: SimpleMockStore,
    ) -> None:
        """Requirement 3e: All 24 hours get a decision."""
        # Only seed hours 1..12 to create partial coverage
        partial_hours = _build_all_hours(
            base_price=50.0, stage="da_anchor",
        )[:12]  # first 12 hours only
        store.seed_predictions(
            RUN_ID, WINTER_DATE, partial_hours,
            stage="da_anchor",
        )

        result = run_seasonal_da_router(WINTER_DATE, store, RUN_ID)

        # 12 decided + 12 missing = 24 total
        assert result["hours_decided"] == 12
        assert result["hours_missing"] == 12
        assert result["hours_decided"] + result["hours_missing"] == 24

    def test_return_dict_structure(
        self, store: SimpleMockStore,
    ) -> None:
        """Requirement 3d: The function returns correct dict with expected keys."""
        all_hours = _build_all_hours(base_price=50.0, stage="da_anchor")
        store.seed_predictions(
            RUN_ID, WINTER_DATE, all_hours,
            stage="da_anchor",
        )

        result = run_seasonal_da_router(WINTER_DATE, store, RUN_ID)

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["target_date"] == WINTER_DATE
        assert result["policy"] == POLICY_NAME
        assert result["selected_model"] == "da_anchor"
        assert result["hours_decided"] == 24
        assert result["hours_missing"] == 0
        assert set(result.keys()) == {
            "status", "target_date", "policy",
            "selected_model", "hours_decided", "hours_missing",
        }

    def test_missing_hours_skipped_gracefully(
        self, store: SimpleMockStore,
    ) -> None:
        """Requirement 3f: Missing hours are skipped gracefully (not crash).

        Only seeds hours that exist (e.g. 1, 3, 5) — gaps should be
        logged but not cause failures.
        """
        for hb in [1, 3, 5]:  # sparse — 21 missing hours
            store.add_hour(
                RUN_ID, WINTER_DATE, hb, float(hb * 10),
                stage="da_anchor",
            )

        # Should not raise
        result = run_seasonal_da_router(WINTER_DATE, store, RUN_ID)

        assert result["hours_decided"] == 3
        assert result["hours_missing"] == 21
        assert result["status"] == "partial"

        written = store.written_decisions[(RUN_ID, WINTER_DATE)]
        assert len(written) == 3
        written_hours = {d["hour_business"] for d in written}
        assert written_hours == {1, 3, 5}

    def test_no_predictions_at_all_returns_failed(
        self, store: SimpleMockStore,
    ) -> None:
        """When no predictions exist at all, status should be 'failed'."""
        result = run_seasonal_da_router(NON_WINTER_DATE, store, RUN_ID)

        assert result["status"] == "failed"
        assert result["hours_decided"] == 0
        assert result["hours_missing"] == 24
        # No decisions should be written
        assert (RUN_ID, NON_WINTER_DATE) not in store.written_decisions

    def test_invalid_pred_price_skipped(
        self, store: SimpleMockStore,
    ) -> None:
        """Hours with invalid/missing pred_price are skipped gracefully."""
        all_hours = _build_all_hours(base_price=50.0, stage="da_anchor")
        # Corrupt hour 7 and 13
        all_hours[6]["pred_price"] = "INVALID"
        all_hours[12].pop("pred_price", None)
        store.seed_predictions(
            RUN_ID, WINTER_DATE, all_hours,
            stage="da_anchor",
        )

        result = run_seasonal_da_router(WINTER_DATE, store, RUN_ID)

        assert result["hours_decided"] == 22
        assert result["hours_missing"] == 2
        assert result["status"] == "partial"

        written = store.written_decisions[(RUN_ID, WINTER_DATE)]
        written_hours = {d["hour_business"] for d in written}
        assert 7 not in written_hours  # invalid pred_price → skipped
        assert 13 not in written_hours  # missing pred_price → skipped
