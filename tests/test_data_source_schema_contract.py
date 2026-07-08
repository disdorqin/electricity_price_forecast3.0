"""Data source schema contract — 002 migration static analysis."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_migration_file_exists():
    assert (PROJECT_ROOT / "db" / "migrations" / "002_data_ingestion.sql").exists()


def test_schema_has_new_tables():
    sql = (PROJECT_ROOT / "db" / "schema.sql").read_text()
    for table in ["efm_data_sources", "efm_source_files", "efm_data_update_runs",
                  "efm_market_data_hourly", "efm_dataset_versions"]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, f"Missing: {table}"


def test_schema_15_tables():
    sql = (PROJECT_ROOT / "db" / "schema.sql").read_text()
    count = sql.count("CREATE TABLE IF NOT EXISTS")
    assert count >= 15, f"Expected >=15 tables, got {count}"


class TestEfmDataSourcesStructure:
    def _sql(self):
        return (PROJECT_ROOT / "db" / "schema.sql").read_text()

    def test_has_source_id_pk(self):
        assert "source_id" in self._sql()

    def test_has_root_path(self):
        assert "root_path" in self._sql()

    def test_has_enabled(self):
        assert "enabled" in self._sql()


class TestEfmSourceFilesStructure:
    def _sql(self):
        return (PROJECT_ROOT / "db" / "schema.sql").read_text()

    def test_has_sha256(self):
        assert "file_sha256" in self._sql()

    def test_has_import_status(self):
        assert "import_status" in self._sql()

    def test_has_unique_key(self):
        assert "uk_source_file" in self._sql()


class TestEfmMarketDataHourlyStructure:
    def _sql(self):
        return (PROJECT_ROOT / "db" / "schema.sql").read_text()

    def test_has_hour_business(self):
        assert "hour_business" in self._sql()

    def test_has_canonical_hour(self):
        sql = self._sql()
        idx = sql.find("efm_market_data_hourly")
        relevant = sql[idx:idx+1000]
        assert "hour_business" in relevant
        assert "TINYINT" in relevant[:relevant.find(") ENGINE")]

    def test_unique_key_date_hour(self):
        assert "uk_market_type_date_hour" in self._sql()


class TestEfmDatasetVersionsStructure:
    def _sql(self):
        return (PROJECT_ROOT / "db" / "schema.sql").read_text()

    def test_has_leakage_cutoff(self):
        assert "leakage_cutoff" in self._sql()

    def test_has_canonical_flag(self):
        assert "canonical_hour_mapping" in self._sql()

    def test_has_status_enum(self):
        assert "ENUM('READY'" in self._sql()
