"""Dashboard view schema/contract tests.

Validates that 003_dashboard_views.sql defines the 8 required views, and (when a
test DB is available) that each view is queryable.
"""

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWS_SQL = REPO_ROOT / "db" / "migrations" / "003_dashboard_views.sql"

REQUIRED_VIEWS = [
    "v_efm_latest_runs",
    "v_efm_run_prediction_counts",
    "v_efm_selected_predictions",
    "v_efm_shadow_safety",
    "v_efm_dataset_readiness",
    "v_efm_postflight_summary",
    "v_efm_delivery_summary",
    "v_efm_hourly_prediction_compare",
]


def _view_names(sql: str):
    names = []
    for line in sql.splitlines():
        line = line.strip()
        if line.upper().startswith("CREATE VIEW"):
            # CREATE VIEW v_efm_xxx AS
            token = line.split()[2]
            names.append(token.rstrip("("))
    return names


def test_views_sql_defines_required_views():
    assert VIEWS_SQL.exists(), "003_dashboard_views.sql missing"
    sql = VIEWS_SQL.read_text(encoding="utf-8")
    names = _view_names(sql)
    for v in REQUIRED_VIEWS:
        assert v in names, f"missing view {v}"

    # shadow_safety must expose the required columns
    assert "shadow_selected_count" in sql
    assert "final_from_shadow_count" in sql
    assert "unsafe_run_count" in sql
    # dataset_readiness must expose the required columns
    assert "leakage_cutoff" in sql
    assert "canonical_hour_mapping" in sql
    # hourly_prediction_compare must expose chart-friendly columns
    assert "is_selected" in sql and "is_shadow" in sql


def test_views_queryable_on_test_db():
    url = os.environ.get("EFM3_TEST_DB_URL")
    if not url:
        pytest.skip("EFM3_TEST_DB_URL not set")
    from common.db.connection import DbConnectionManager

    mgr = DbConnectionManager(db_url=url)
    conn = mgr.get_connection()
    cur = conn.cursor()
    for v in REQUIRED_VIEWS:
        cur.execute(f"SELECT 1 FROM {v} LIMIT 1")
    mgr.close()
