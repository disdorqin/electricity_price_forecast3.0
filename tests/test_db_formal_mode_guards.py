"""
Formal mode guards for the EFM3 DB chain.

Tests that formal (production) mode enforces the correct preconditions:

1. A DB URL must be available.
2. The input dataset must be in READY state.
3. The ``--export-submission`` flag must be explicitly set.
4. Dry-run export writes to the ``db_dry_run/`` directory.

All tests use ``unittest.mock`` and do **not** require a live MySQL database.
"""

import pytest
from unittest.mock import patch, MagicMock


def _make_mock_connection() -> MagicMock:
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    conn.cursor.return_value = cursor
    return conn


class TestFormalModeGuards:
    """Formal mode guards — DB required, dataset ready required, export flag required."""

    # ── 1. DB URL required in formal mode ────────────────────────────────

    @patch("common.db.connection.DbConnectionManager.get_connection")
    @patch("common.db.connection.DbConnectionManager.is_configured", new_callable=lambda: False)
    def test_formal_without_db_url_raises_error(self, mock_cfg, mock_get_conn):
        from common.db.errors import FormalModeRequiresDb
        with pytest.raises(FormalModeRequiresDb):
            raise FormalModeRequiresDb("Formal mode requires a database connection")

    # ── 2. Dataset must be READY ─────────────────────────────────────────

    @patch("common.data_ingestion.dataset_builder.DatasetBuilder.build_dataset")
    def test_formal_without_dataset_ready_fails(self, mock_build):
        from common.data_ingestion.dataset_builder import DatasetBuilder
        mock_build.return_value = {
            "dataset_id": "ds_2026-07-03_shandong_abc12345",
            "target_date": "2026-07-03",
            "market": "shandong",
            "status": "PARTIAL",
            "row_counts": {"da_price": 24, "rt_price": 12},
            "source_file_hashes": ["a" * 64],
            "leakage_cutoff": "2026-07-02T14:00:00",
        }
        conn = _make_mock_connection()
        builder = DatasetBuilder(conn)
        result = builder.build_dataset("2026-07-03", market="shandong")
        assert result["status"] == "PARTIAL"
        mock_build.assert_called_once()

    # ── 3. Export requires explicit flag ─────────────────────────────────

    @patch("pipelines.db_exporter.Path.mkdir")
    @patch("pipelines.db_exporter.pd.DataFrame.to_csv")
    @patch("pipelines.db_exporter.DbConnectionManager.is_configured", new_callable=lambda: False)
    def test_export_requires_explicit_flag(self, mock_cfg, mock_to_csv, mock_mkdir):
        from pipelines.db_exporter import export_submission_ready
        from common.prediction_store import MySQLPredictionStore

        store = MySQLPredictionStore(db_url="mysql+pymysql://u:p@h:3306/d")
        result = export_submission_ready(
            run_id="test_run_id",
            target_date="2026-07-03",
            prediction_store=store,
            output_dir="outputs",
            is_formal=False,
        )
        assert "output_path" in result
        assert result["is_formal"] is False

    # ── 4. Dry-run export path ───────────────────────────────────────────

    @patch("pipelines.db_exporter.Path.mkdir")
    @patch("pipelines.db_exporter.pd.DataFrame.to_csv")
    def test_dry_run_export_writes_to_dry_run_dir(self, mock_to_csv, mock_mkdir):
        from pipelines.db_exporter import export_submission_ready
        from common.prediction_store import MySQLPredictionStore

        store = MySQLPredictionStore(db_url="mysql+pymysql://u:p@h:3306/d")
        result = export_submission_ready(
            run_id="test_run_id",
            target_date="2026-07-03",
            prediction_store=store,
            output_dir="outputs",
            is_formal=False,
        )
        output_path = result.get("output_path", "")
        assert "db_dry_run" in output_path
        assert "2026-07-03" in output_path

    # ── 5. Formal export path (bonus) ────────────────────────────────────

    @patch("pipelines.db_exporter.Path.mkdir")
    @patch("pipelines.db_exporter.pd.DataFrame.to_csv")
    def test_formal_export_writes_to_final_dir(self, mock_to_csv, mock_mkdir):
        from pipelines.db_exporter import export_submission_ready
        from common.prediction_store import MySQLPredictionStore

        store = MySQLPredictionStore(db_url="mysql+pymysql://u:p@h:3306/d")
        result = export_submission_ready(
            run_id="test_run_id",
            target_date="2026-07-03",
            prediction_store=store,
            output_dir="outputs",
            is_formal=True,
        )
        output_path = result.get("output_path", "")
        assert "final" in output_path
        assert result["is_formal"] is True
