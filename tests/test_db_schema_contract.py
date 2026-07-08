"""
Tests for the EFM3 DB schema contract.

All tests perform static analysis on db/schema.sql — no live MySQL
connection is required.  They verify structural invariants that the
production pipeline relies on.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_schema() -> str:
    """Return the full text of the schema file."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _tables_present(sql: str) -> dict[str, str]:
    """Return {table_name: create_statement} for every CREATE TABLE found."""
    blocks: dict[str, str] = {}
    for raw in sql.split("CREATE TABLE IF NOT EXISTS "):
        if "(" not in raw:
            continue
        name = raw[: raw.index("(")].strip()
        # Strip surrounding backticks if any
        name = name.strip("`")
        # Grab the text from the opening paren to the final ENGINE clause
        depth = 0
        start = raw.index("(")
        for i, ch in enumerate(raw[start:], start=start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    blocks[name] = raw[start : i + 1]
                    break
    return blocks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def schema_sql() -> str:
    """Load the schema file once per session."""
    return _load_schema()


@pytest.fixture(scope="session")
def tables(schema_sql: str) -> dict[str, str]:
    """Map of {table_name: body} for every table in the schema."""
    return _tables_present(schema_sql)


# ===================================================================
# 1.  Schema file existence
# ===================================================================

class TestSchemaFile:
    """The schema file must exist and be non-empty."""

    def test_schema_file_exists(self) -> None:
        assert SCHEMA_PATH.is_file(), f"Schema file not found: {SCHEMA_PATH}"

    def test_schema_file_nonempty(self) -> None:
        text = _load_schema()
        assert len(text.strip()) > 0, "Schema file is empty"


# ===================================================================
# 2.  Core tables
# ===================================================================

CORE_TABLES = [
    "efm_runs",
    "efm_actual_prices",
    "efm_feature_snapshots",
    "efm_predictions",
    "efm_fusion_decisions",
    "efm_postflight_checks",
    "efm_delivery_outputs",
    "efm_model_registry",
    "efm_run_events",
    "efm_artifacts",
]


class TestCoreTablesExist:
    """All 10 core production tables must be defined in the schema."""

    def test_ten_tables_defined(self, tables: dict[str, str]) -> None:
        assert len(tables) >= len(CORE_TABLES), (
            f"Expected at least {len(CORE_TABLES)} tables, "
            f"found {len(tables)}"
        )

    # -- individual table tests -----------------------------------------

    def test_efm_runs_table(self, tables: dict[str, str]) -> None:
        assert "efm_runs" in tables, "efm_runs table is missing"

    def test_efm_actual_prices_table(self, tables: dict[str, str]) -> None:
        assert "efm_actual_prices" in tables, (
            "efm_actual_prices table is missing"
        )

    def test_efm_feature_snapshots_table(self, tables: dict[str, str]) -> None:
        assert "efm_feature_snapshots" in tables, (
            "efm_feature_snapshots table is missing"
        )

    def test_efm_predictions_table(self, tables: dict[str, str]) -> None:
        assert "efm_predictions" in tables, (
            "efm_predictions table is missing"
        )

    def test_efm_fusion_decisions_table(self, tables: dict[str, str]) -> None:
        assert "efm_fusion_decisions" in tables, (
            "efm_fusion_decisions table is missing"
        )

    def test_efm_postflight_checks_table(self, tables: dict[str, str]) -> None:
        assert "efm_postflight_checks" in tables, (
            "efm_postflight_checks table is missing"
        )

    def test_efm_delivery_outputs_table(self, tables: dict[str, str]) -> None:
        assert "efm_delivery_outputs" in tables, (
            "efm_delivery_outputs table is missing"
        )

    def test_efm_model_registry_table(self, tables: dict[str, str]) -> None:
        assert "efm_model_registry" in tables, (
            "efm_model_registry table is missing"
        )

    def test_efm_run_events_table(self, tables: dict[str, str]) -> None:
        assert "efm_run_events" in tables, (
            "efm_run_events table is missing"
        )

    def test_efm_artifacts_table(self, tables: dict[str, str]) -> None:
        assert "efm_artifacts" in tables, (
            "efm_artifacts table is missing"
        )


# ===================================================================
# 3.  efm_predictions — structural invariants
# ===================================================================

class TestEfmPredictionsStructure:
    """Check detailed column / constraint requirements on efm_predictions."""

    SCHEMA_BODY: str = ""

    @pytest.fixture(autouse=True)
    def _load_predictions_body(self, tables: dict[str, str]) -> None:
        # Make the body available as an instance attribute so each test can
        # use it without repeating the fixture lookup.
        type(self).SCHEMA_BODY = tables.get("efm_predictions", "")

    # -- c. UNIQUE KEY on (run_id, target_date, hour_business, stage) ---

    def test_unique_key_run_date_hour_stage(self) -> None:
        body = self.SCHEMA_BODY
        assert (
            "UNIQUE KEY" in body
            and "run_id" in body
            and "target_date" in body
            and "hour_business" in body
            and "stage" in body
        ), (
            "Missing UNIQUE KEY on (run_id, target_date, hour_business, stage) "
            "in efm_predictions"
        )
        # Verify the specific constraint name if present
        assert "uk_run_date_hour_stage" in body, (
            "Expected UNIQUE KEY named 'uk_run_date_hour_stage'"
        )

    # -- e. hour_business TINYINT ---------------------------------------

    def test_hour_business_tinyint(self) -> None:
        body = self.SCHEMA_BODY
        assert "hour_business" in body and "TINYINT" in body, (
            "efm_predictions.hour_business must be TINYINT"
        )

    # -- f. is_shadow and is_selected as BOOLEAN -----------------------

    def test_is_shadow_boolean(self) -> None:
        body = self.SCHEMA_BODY
        assert "is_shadow" in body and "BOOLEAN" in body, (
            "efm_predictions.is_shadow must be BOOLEAN"
        )

    def test_is_selected_boolean(self) -> None:
        body = self.SCHEMA_BODY
        assert "is_selected" in body and "BOOLEAN" in body, (
            "efm_predictions.is_selected must be BOOLEAN"
        )

    # -- g. FOREIGN KEY to efm_runs ------------------------------------

    def test_fk_to_efm_runs(self) -> None:
        body = self.SCHEMA_BODY
        assert (
            "FOREIGN KEY" in body
            and "run_id" in body
            and "REFERENCES efm_runs" in body
        ), (
            "efm_predictions must have a FOREIGN KEY referencing efm_runs(run_id)"
        )


# ===================================================================
# 4.  efm_runs — PRIMARY KEY
# ===================================================================

class TestEfmRunsPrimaryKey:
    """efm_runs.run_id must be the PRIMARY KEY."""

    def test_run_id_is_primary_key(self, tables: dict[str, str]) -> None:
        body = tables.get("efm_runs", "")
        assert "run_id" in body and "PRIMARY KEY" in body, (
            "efm_runs.run_id must be defined as PRIMARY KEY"
        )


# ===================================================================
# 5.  FOREIGN KEY references across tables
# ===================================================================

class TestForeignKeyReferences:
    """Verify that all tables with FK constraints reference them correctly."""

    def _body(self, tables: dict[str, str], name: str) -> str:
        return tables.get(name, "")

    # -- efm_feature_snapshots -> efm_runs ----------------------------

    def test_feature_snapshots_fk(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_feature_snapshots")
        assert (
            "FOREIGN KEY" in body
            and "REFERENCES efm_runs" in body
        ), "efm_feature_snapshots missing FK to efm_runs"

    # -- efm_predictions -> efm_runs (already checked above, but
    #    included for completeness of the FK sweep) --------------------

    def test_predictions_fk(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_predictions")
        assert (
            "FOREIGN KEY" in body
            and "REFERENCES efm_runs" in body
        ), "efm_predictions missing FK to efm_runs"

    # -- efm_fusion_decisions -> efm_runs & efm_predictions -----------

    def test_fusion_decisions_fk_runs(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_fusion_decisions")
        assert (
            "FOREIGN KEY" in body
            and "REFERENCES efm_runs" in body
        ), "efm_fusion_decisions missing FK to efm_runs"

    def test_fusion_decisions_fk_predictions(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_fusion_decisions")
        assert (
            "REFERENCES efm_predictions" in body
        ), "efm_fusion_decisions missing FK to efm_predictions(id)"

    # -- efm_postflight_checks -> efm_runs ----------------------------

    def test_postflight_checks_fk(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_postflight_checks")
        assert (
            "FOREIGN KEY" in body
            and "REFERENCES efm_runs" in body
        ), "efm_postflight_checks missing FK to efm_runs"

    # -- efm_delivery_outputs -> efm_runs -----------------------------

    def test_delivery_outputs_fk(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_delivery_outputs")
        assert (
            "FOREIGN KEY" in body
            and "REFERENCES efm_runs" in body
        ), "efm_delivery_outputs missing FK to efm_runs"

    # -- efm_run_events -> efm_runs -----------------------------------

    def test_run_events_fk(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_run_events")
        assert (
            "FOREIGN KEY" in body
            and "REFERENCES efm_runs" in body
        ), "efm_run_events missing FK to efm_runs"

    # -- efm_artifacts -> efm_runs ------------------------------------

    def test_artifacts_fk(self, tables: dict[str, str]) -> None:
        body = self._body(tables, "efm_artifacts")
        assert (
            "FOREIGN KEY" in body
            and "REFERENCES efm_runs" in body
        ), "efm_artifacts missing FK to efm_runs"


# ===================================================================
# 6.  Sanity — no obvious DDL breakage
# ===================================================================

class TestSchemaSanity:
    """Quick sanity checks to catch obvious typos or truncation."""

    def test_no_truncated_create(self, schema_sql: str) -> None:
        """Every CREATE TABLE should have a closing ENGINE clause."""
        fragments = schema_sql.split("CREATE TABLE IF NOT EXISTS ")[1:]
        for stmt in fragments:
            if not stmt.strip():
                continue
            assert "ENGINE" in stmt, (
                f"CREATE TABLE statement missing ENGINE clause:\n  {stmt[:120]}..."
            )

    def test_utf8mb4_consistency(self, schema_sql: str) -> None:
        """All tables should declare utf8mb4 as the charset."""
        fragments = schema_sql.split("CREATE TABLE IF NOT EXISTS ")[1:]
        for stmt in fragments:
            if not stmt.strip():
                continue
            assert "utf8mb4" in stmt, (
                f"Table missing utf8mb4 charset:\n  {stmt[:120]}..."
            )

    def test_innodb_all_tables(self, schema_sql: str) -> None:
        """All tables must use InnoDB engine."""
        fragments = schema_sql.split("CREATE TABLE IF NOT EXISTS ")[1:]
        for stmt in fragments:
            if not stmt.strip():
                continue
            assert "InnoDB" in stmt, (
                f"Table not using InnoDB engine:\n  {stmt[:120]}..."
            )
