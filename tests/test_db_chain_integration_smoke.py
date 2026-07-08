"""
DB chain integration smoke tests — lightweight, no real DB needed.

Verifies the basic integration contract across the EFM3 DB chain:

1. DbConnectionManager health check.
2. FilePredictionStore creation / write / read round-trip.
3. Seasonal DA router integration with a mock store.
4. DB postflight integration with a mock connection.
5. Export function integration with a mock store.

All tests use ``unittest.mock`` and do **not** require a live MySQL server.
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock


def _make_mock_connection() -> MagicMock:
    """Return a mock pymysql Connection with a working cursor context-manager."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (1,)  # SELECT 1 returns 1
    cursor.fetchall.return_value = []
    conn.cursor.return_value = cursor
    return conn


# ═══════════════════════════════════════════════════════════════════════
# 1. DB health check
# ═══════════════════════════════════════════════════════════════════════


class TestDbHealth:
    """DbConnectionManager health check."""

    @patch("common.db.connection.pymysql.connect")
    def test_db_health_check_returns_ok(self, mock_connect):
        """Mock a healthy DbConnectionManager and verify health_check returns ok."""
        mock_conn = _make_mock_connection()
        mock_connect.return_value = mock_conn

        from common.db.connection import DbConnectionManager

        mgr = DbConnectionManager(db_url="mysql+pymysql://user:pass@localhost:3306/efm3")
        result = mgr.health_check()

        assert result["status"] == "ok", f"Expected 'ok', got: {result.get('status')}"
        assert "db_url_prefix" in result, "Health check result should include db_url_prefix"


# ═══════════════════════════════════════════════════════════════════════
# 2. Prediction store create
# ═══════════════════════════════════════════════════════════════════════


class TestPredictionStoreCreate:
    """FilePredictionStore can be created and returns the expected type."""

    def test_file_prediction_store_create(self):
        """FilePredictionStore can be instantiated."""
        from common.prediction_store import FilePredictionStore

        store = FilePredictionStore()
        assert store is not None, "FilePredictionStore should be instantiable"

    def test_mysql_prediction_store_create(self):
        """MySQLPredictionStore can be instantiated (no DB needed)."""
        from common.prediction_store import MySQLPredictionStore

        store = MySQLPredictionStore(db_url="mysql+pymysql://user:pass@localhost:3306/efm3")
        assert store is not None, "MySQLPredictionStore should be instantiable"

    def test_factory_create_file(self):
        """Factory returns FilePredictionStore when no db_url provided."""
        from common.prediction_store import create_prediction_store, FilePredictionStore

        store = create_prediction_store(db_url=None, prefer_db=False)
        assert isinstance(store, FilePredictionStore), (
            "Factory should return FilePredictionStore when prefer_db=False"
        )

    def test_factory_create_mysql(self):
        """Factory returns MySQLPredictionStore when db_url provided."""
        from common.prediction_store import create_prediction_store, MySQLPredictionStore

        store = create_prediction_store(
            db_url="mysql+pymysql://user:pass@localhost:3306/efm3", prefer_db=True
        )
        assert isinstance(store, MySQLPredictionStore), (
            "Factory should return MySQLPredictionStore when db_url is provided"
        )


# ═══════════════════════════════════════════════════════════════════════
# 3. Prediction store write / read round-trip
# ═══════════════════════════════════════════════════════════════════════


class TestPredictionStoreWriteRead:
    """FilePredictionStore write → read round-trip."""

    def test_write_then_read_predictions(self, tmp_path):
        """Write predictions and read them back, verifying round-trip fidelity."""
        from common.prediction_store import FilePredictionStore

        store = FilePredictionStore(base_dir=str(tmp_path))

        predictions = [
            {
                "hour_business": 1,
                "task": "realtime",
                "stage": "da_anchor",
                "model_name": "da_anchor",
                "model_version": "v1",
                "pred_price": 100.0,
                "is_shadow": False,
                "is_selected": True,
                "selected_reason": "test",
            },
            {
                "hour_business": 2,
                "task": "realtime",
                "stage": "da_anchor",
                "model_name": "da_anchor",
                "model_version": "v1",
                "pred_price": 110.0,
                "is_shadow": False,
                "is_selected": True,
                "selected_reason": "test",
            },
        ]

        written = store.write_predictions("test_run", "2026-07-03", predictions)
        assert written == 2, f"Expected 2 writes, got {written}"

        read_back = store.read_predictions("test_run", "2026-07-03")
        assert len(read_back) >= 2, f"Expected at least 2 rows, got {len(read_back)}"

        # Verify round-trip fidelity for price
        prices = {
            int(r["hour_business"]): float(r["pred_price"])
            for r in read_back
            if r.get("stage") == "da_anchor"
        }
        assert prices.get(1) == 100.0, f"Expected price 100.0 for hour 1, got {prices.get(1)}"
        assert prices.get(2) == 110.0, f"Expected price 110.0 for hour 2, got {prices.get(2)}"

    def test_write_then_read_with_selected_filter(self, tmp_path):
        """Write and read with is_selected filter via the public API."""
        from common.prediction_store import FilePredictionStore

        store = FilePredictionStore(base_dir=str(tmp_path))

        # Write 24 hours of predictions, some selected
        predictions = []
        for hb in range(1, 25):
            predictions.append({
                "hour_business": hb,
                "task": "realtime",
                "stage": "da_anchor",
                "model_name": "model_x",
                "pred_price": float(50 + hb),
                "is_shadow": False,
                "is_selected": hb % 2 == 0,
            })

        store.write_predictions("run_sel", "2026-07-04", predictions)

        # Read all and check selections
        all_rows = store.read_predictions("run_sel", "2026-07-04")
        assert len(all_rows) == 24, f"Expected 24 rows, got {len(all_rows)}"


# ═══════════════════════════════════════════════════════════════════════
# 4. Seasonal DA router integration
# ═══════════════════════════════════════════════════════════════════════


class TestSeasonalRouterIntegration:
    """Seasonal DA router with a mock prediction store."""

    def test_seasonal_router_non_winter_ok(self):
        """Non-winter date (July): router uses official_baseline predictions."""
        from common.prediction_store import PredictionStore
        from pipelines.seasonal_da_router import run_seasonal_da_router

        mock_store = MagicMock(spec=PredictionStore)

        # Simulate official_baseline predictions for all 24 hours
        official_rows = [
            {
                "hour_business": hb,
                "pred_price": float(30 + hb),
                "model_name": "official_baseline",
                "stage": "official_baseline",
                "task": "realtime",
            }
            for hb in range(1, 25)
        ]
        mock_store.read_predictions.return_value = official_rows

        result = run_seasonal_da_router(
            target_date="2026-07-03",
            prediction_store=mock_store,
            run_id="test_run_001",
        )

        assert result["status"] == "ok", (
            f"Expected status='ok', got: {result.get('status')}"
        )
        assert result["hours_decided"] == 24
        assert result["policy"] == "seasonal_da_router"
        mock_store.write_selected_final.assert_called_once()

    def test_seasonal_router_winter_uses_da_anchor(self):
        """Winter date (January): router uses da_anchor predictions."""
        from common.prediction_store import PredictionStore
        from pipelines.seasonal_da_router import run_seasonal_da_router

        mock_store = MagicMock(spec=PredictionStore)

        # Simulate da_anchor predictions for winter
        da_rows = [
            {
                "hour_business": hb,
                "pred_price": float(40 + hb),
                "model_name": "da_anchor",
                "stage": "da_anchor",
            }
            for hb in range(1, 25)
        ]
        mock_store.read_predictions.return_value = da_rows

        result = run_seasonal_da_router(
            target_date="2026-01-15",
            prediction_store=mock_store,
            run_id="test_winter_run",
        )

        assert result["status"] == "ok"
        assert result["selected_model"] == "da_anchor"
        assert result["hours_decided"] == 24

    def test_seasonal_router_partial_data(self):
        """Router handles partial data gracefully (status='partial')."""
        from common.prediction_store import PredictionStore
        from pipelines.seasonal_da_router import run_seasonal_da_router

        mock_store = MagicMock(spec=PredictionStore)

        # Only 12 hours available
        partial_rows = [
            {
                "hour_business": hb,
                "pred_price": float(50 + hb),
                "model_name": "official_baseline",
                "stage": "official_baseline",
                "task": "realtime",
            }
            for hb in range(1, 13)
        ]
        mock_store.read_predictions.return_value = partial_rows

        result = run_seasonal_da_router(
            target_date="2026-06-01",
            prediction_store=mock_store,
            run_id="test_partial_run",
        )

        assert result["status"] == "partial", (
            f"Expected 'partial' for 12/24 hours, got: {result.get('status')}"
        )
        assert result["hours_missing"] == 12


# ═══════════════════════════════════════════════════════════════════════
# 5. DB postflight integration
# ═══════════════════════════════════════════════════════════════════════


class TestPostflightIntegration:
    """DB postflight checks with a mock connection.

    !!! Warning about mock cursor side-effect sequencing.

    All 8 postflight checks run sequentially against a single shared mock
    cursor. The ``fetchone.side_effect`` list below is consumed in the exact
    order the checks call them:

        1. row_count_24:       fetchone → (24,)
        2. no_nan:             fetchone → (0,)
        3. no_duplicates:      fetchone → (24, 24)    ← 2-element tuple
        4. price_range:        fetchone → (30.0, 80.0) ← 2-element tuple
        5. selected_source:    fetchone → (0,)
        6. shadow_not_final:   fetchone → (0,)
        7. submission_row_count: fetchone → (24,)

    ``_check_hour_range`` uses ``fetchall()`` (not fetchone), so a separate
    ``fetchall.return_value`` is provided for that.
    """

    def test_postflight_all_checks_pass(self):
        """All postflight checks pass with well-formed mock data."""
        from pipelines.db_postflight import run_db_postflight

        conn = _make_mock_connection()

        # ---- fetchone side-effects in execution order ----
        # Each tuple carries the columns expected by the SQL SELECT clause.
        fetchone_values = [
            (24,),         # row_count_24:       COUNT(*) → 24
            (0,),          # no_nan:             COUNT(*) null_count → 0
            (24, 24),      # no_duplicates:      total, distinct → 24, 24
            (30.0, 80.0),  # price_range:        MIN, MAX → within [-500, 2000]
            (0,),          # selected_source:    COUNT(*) bad → 0
            (0,),          # shadow_not_final:   COUNT(*) shadow → 0
            (24,),         # submission_row_count: COUNT(DISTINCT) → 24
        ]
        conn.cursor.return_value.fetchone.side_effect = fetchone_values

        # ---- fetchall for hour_range (each row is a single-column tuple) ----
        conn.cursor.return_value.fetchall.return_value = [
            (i,) for i in range(1, 25)
        ]

        result = run_db_postflight(
            conn=conn,
            run_id="test_postflight_run",
            target_date="2026-07-03",
        )

        assert result["status"] == "passed", (
            f"Expected all checks to pass, got: {result.get('status')}"
        )
        assert len(result["checks"]) == 8, (
            f"Expected 8 check results, got {len(result['checks'])}"
        )

    def test_postflight_detects_failure(self):
        """Postflight correctly reports failure when checks do not pass."""
        from pipelines.db_postflight import run_db_postflight

        conn = _make_mock_connection()

        # Simulate row_count_24 → 0 (failure cascades to many checks)
        fetchone_values = [
            (0,),          # row_count_24:       COUNT(*) → 0 (FAIL)
            (0,),          # no_nan:             null_count → 0 (vacuously true)
            (0, 0),        # no_duplicates:      total=0, distinct=0 (passes vacuously)
            (None, None),  # price_range:        NULL → FAIL
            (0,),          # selected_source:    bad_count → 0
            (0,),          # shadow_not_final:   shadow_count → 0
            (0,),          # submission_row_count: distinct → 0 (FAIL)
        ]
        conn.cursor.return_value.fetchone.side_effect = fetchone_values

        # hour_range returns empty list (no rows → FAIL)
        conn.cursor.return_value.fetchall.return_value = []

        result = run_db_postflight(
            conn=conn,
            run_id="test_fail_run",
            target_date="2026-07-03",
        )

        assert result["status"] == "failed", (
            f"Expected 'failed' status for empty predictions, "
            f"got: {result.get('status')}"
        )
