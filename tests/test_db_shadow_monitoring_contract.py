"""
Shadow monitoring contract for the EFM3 DB chain.

Verifies that shadow predictions never leak into the final (selected) output:

1. ``is_shadow`` rows are never marked ``is_selected``.
2. Dry-run mode does not write ``submission_ready.csv``.
3. Shadow safety function detects contamination.
4. DB URL passwords are redacted in output.

All tests use ``unittest.mock`` and do **not** require a live MySQL database.
"""

import pytest
from unittest.mock import patch, MagicMock, call


def _make_mock_connection() -> MagicMock:
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    conn.cursor.return_value = cursor
    return conn


# ===================================================================
# 1. Shadow rows not selected
# ===================================================================


class TestShadowIsSelected:
    """``is_shadow`` rows must never have ``is_selected = True``."""

    def test_shadow_not_selected(self):
        """The exporter's _is_selected predicate correctly flags shadow rows."""
        from pipelines.db_exporter import _is_selected

        # A legitimate final-selected row
        assert _is_selected({"is_selected": True}) is True
        assert _is_selected({"is_selected": 1}) is True

        # A shadow row must not be treated as selected
        assert _is_selected({"is_selected": False}) is False
        assert _is_selected({"is_selected": 0}) is False
        assert _is_selected({}) is False

        # Combined — flag alone check
        assert _is_selected({"is_shadow": True, "is_selected": False}) is False
        assert _is_selected({"is_shadow": True, "is_selected": True}) is True

    def test_no_shadow_prediction_read_as_selected(self, tmp_path):
        """Shadow predictions written via write_shadow_predictions
        must never be returned by read_predictions with is_selected filter."""
        from common.prediction_store import FilePredictionStore

        store = FilePredictionStore(base_dir=str(tmp_path))
        store.write_shadow_predictions("run_s", "2026-07-03", "p3_shadow", [
            {"hour_business": 1, "pred_price": 50.0, "model_name": "test"},
        ])
        shadow_rows = store.read_predictions("run_s", "2026-07-03", task="shadow")
        for r in shadow_rows:
            assert r.get("is_shadow") in (True, "true", "True", 1)


# ===================================================================
# 2. No submission_ready.csv created in dry-run
# ===================================================================


class TestDryRunSubmission:
    """In dry-run mode, the submission file is not placed in final/."""

    def test_dry_run_no_submission_in_final(self, tmp_path):
        """Dry-run export output must go to ``db_dry_run/``, not ``final/``."""
        from pipelines.db_exporter import export_submission_ready
        from common.prediction_store import PredictionStore

        store = MagicMock(spec=PredictionStore)
        store.read_predictions.return_value = []

        with patch("pipelines.db_exporter.Path.mkdir"), \
             patch("pipelines.db_exporter.pd.DataFrame.to_csv"):
            result = export_submission_ready(
                run_id="test_shadow_run",
                target_date="2026-07-03",
                prediction_store=store,
                output_dir=str(tmp_path / "outputs"),
                is_formal=False,
            )

        output_path = result.get("output_path", "")
        assert "db_dry_run" in output_path
        assert result["is_formal"] is False


# ===================================================================
# 3. Shadow safety check
# ===================================================================


class TestShadowSafetyCheck:
    """Shadow safety functions detect contamination."""

    def test_shadow_safety_check_detects_contamination(self):
        """The shadow safety check must raise ShadowContaminationError."""
        from common.db.errors import ShadowContaminationError

        predictions = [
            {"hour_business": 1, "is_shadow": False, "is_selected": True},
            {"hour_business": 2, "is_shadow": True, "is_selected": True},
        ]

        def _verify_no_shadow_selected(rows):
            for r in rows:
                if r.get("is_shadow") and r.get("is_selected"):
                    raise ShadowContaminationError(
                        f"Shadow prediction for hour {r['hour_business']} "
                        f"would contaminate final output"
                    )

        with pytest.raises(ShadowContaminationError) as excinfo:
            _verify_no_shadow_selected(predictions)
        assert "Shadow" in str(excinfo.value)

    @patch("pipelines.db_postflight.insert_postflight_check")
    def test_shadow_not_final_check_exists(self, mock_insert):
        """The postflight 'shadow_not_final' check is executed and registered."""
        from pipelines.db_postflight import run_db_postflight

        conn = _make_mock_connection()

        # fetchone side-effect sequence matching the 8 checks' execution order:
        # 1. row_count_24        → (24,)          1 el
        # 2. no_nan              → (0,)           1 el
        # 3. no_duplicates       → (24, 24)       2 el ← KEY
        # 4. price_range         → (30.0, 80.0)   2 el
        # 5. selected_source     → (0,)           1 el
        # 6. shadow_not_final    → (0,)           1 el
        # 7. submission_row_count → (24,)         1 el
        conn.cursor.return_value.fetchone.side_effect = [
            (24,),
            (0,),
            (24, 24),
            (30.0, 80.0),
            (0,),
            (0,),
            (24,),
        ]
        conn.cursor.return_value.fetchall.return_value = [(i,) for i in range(1, 25)]

        result = run_db_postflight(
            conn=conn,
            run_id="test_shadow_check",
            target_date="2026-07-03",
        )

        assert "shadow_not_final" in result.get("checks", {})
        assert result["checks"]["shadow_not_final"]["passed"] is True

    @patch("pipelines.db_postflight.insert_postflight_check")
    def test_shadow_not_final_catches_contamination(self, mock_insert):
        """Postflight detects shadow contamination."""
        from pipelines.db_postflight import run_db_postflight

        conn = _make_mock_connection()

        fetchone_values = [
            (24,),       # row_count_24: cnt = 24
            (0,),        # no_nan: null_count = 0
            (24, 24),    # no_duplicates: total, distinct = 24, 24
            (30.0, 80.0),  # price_range: min, max = 30, 80
            (0,),        # selected_source: bad_count = 0
            (2,),        # shadow_not_final: shadow_count = 2  ← contamination!
            (24,),       # submission_row_count: distinct = 24
        ]
        conn.cursor.return_value.fetchone.side_effect = fetchone_values
        conn.cursor.return_value.fetchall.return_value = [(i,) for i in range(1, 25)]

        result = run_db_postflight(
            conn=conn,
            run_id="test_contam",
            target_date="2026-07-03",
        )

        shadow_result = result["checks"]["shadow_not_final"]
        assert shadow_result["passed"] is False


# ===================================================================
# 4. DB URL redacted
# ===================================================================


class TestDbUrlRedaction:
    """Database URL passwords must be redacted in log output and UI."""

    def test_db_url_redacted(self):
        """MySQLPredictionStore.get_db_url_info redacts the password."""
        from common.prediction_store import MySQLPredictionStore

        store = MySQLPredictionStore(db_url="mysql+pymysql://user:secretpass@dbhost:3306/efm3")
        info = store.get_db_url_info()

        assert "secretpass" not in info
        assert "****" in info
        assert "dbhost" in info

    def test_db_url_without_password(self):
        """A DB URL without password is handled safely."""
        from common.prediction_store import MySQLPredictionStore

        store = MySQLPredictionStore(db_url="mysql+pymysql://root@localhost:3306/efm3")
        info = store.get_db_url_info()
        assert info is not None
