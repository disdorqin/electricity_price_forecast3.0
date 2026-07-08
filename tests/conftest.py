"""Shared pytest fixtures for EFM3 Control Plane backend tests.

DB-backed tests are ENV-GATED: they only run when EFM3_TEST_DB_URL points at a
dedicated test database. They NEVER use the production ledger and never hardcode
credentials. If the env var is unset, DB tests skip cleanly.

To avoid cross-test state leakage, every test function explicitly sets (and
restores) ``settings.db_url`` (and the API-key knobs) via function-scoped fixtures.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# FastAPI is only available in the backend venv, NOT in the conda `epf-2`
# environment that runs the CLI / pipeline / shadow-registry tests. Guard the
# import so this conftest loads cleanly under both interpreters; the API
# fixtures below are only registered when FastAPI is present.
try:
    from fastapi.testclient import TestClient  # noqa: E402
    from backend.app.main import app  # noqa: E402
    from backend.app.config import settings  # noqa: E402
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover - exercised only in the non-backend env
    _HAS_FASTAPI = False


if _HAS_FASTAPI:

    @pytest.fixture
    def client():
        """TestClient with NO database configured (graceful 503 paths)."""
        saved = (settings.db_url, settings.api_key, settings.ops_allow_from, settings.ops_enabled)
        settings.db_url = ""
        settings.api_key = ""
        settings.ops_allow_from = "127.0.0.1,::1,testclient,localhost"
        settings.ops_enabled = False  # pin "disabled by default" regardless of local .env
        yield TestClient(app)
        settings.db_url, settings.api_key, settings.ops_allow_from, settings.ops_enabled = saved

    @pytest.fixture
    def db_client(_test_db_url):
        """TestClient pointed at the freshly built test database."""
        saved = (settings.db_url, settings.api_key, settings.ops_allow_from, settings.ops_enabled)
        settings.db_url = _test_db_url
        settings.api_key = ""
        settings.ops_allow_from = "127.0.0.1,::1,testclient,localhost"
        settings.ops_enabled = False  # pin "disabled by default" regardless of local .env
        yield TestClient(app)
        settings.db_url, settings.api_key, settings.ops_allow_from, settings.ops_enabled = saved


@pytest.fixture(scope="session")
def _test_db_url():
    """Build a fresh test database (schema + views + fixtures); yield its URL.

    Skips if EFM3_TEST_DB_URL is not set. Uses a dedicated DB only.
    """
    url = os.environ.get("EFM3_TEST_DB_URL")
    if not url:
        pytest.skip("EFM3_TEST_DB_URL not set — skipping DB-backed tests")

    from common.db.connection import DbConnectionManager
    from common.db.schema import init_schema

    mgr = DbConnectionManager(db_url=url)
    params = mgr._parse_url()
    import pymysql

    srv = pymysql.connect(
        host=params["host"], port=params["port"], user=params["user"],
        password=params["password"], connect_timeout=10,
    )
    dbname = params["database"]
    srv.cursor().execute(f"DROP DATABASE IF EXISTS `{dbname}`")
    srv.cursor().execute(f"CREATE DATABASE `{dbname}` CHARACTER SET utf8mb4")
    srv.close()

    conn = mgr.get_connection()
    init_schema(conn)

    views_sql = (REPO_ROOT / "db" / "migrations" / "003_dashboard_views.sql").read_text(encoding="utf-8")
    for raw in views_sql.split(";"):
        lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt and not stmt.upper().startswith("SET @"):
            try:
                conn.cursor().execute(stmt)
            except Exception:
                pass
    conn.commit()

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO efm_runs (run_id,target_date,chain_version,mode,status,delivery_status,exit_code,started_at,finished_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())",
        ("test_run_1", "2026-01-15", "3.0-db-ledger-v1", "dry_run", "COMPLETE", "NORMAL", 0),
    )
    cur.execute(
        "INSERT INTO efm_dataset_versions (dataset_id,target_date,status,row_counts,leakage_cutoff,canonical_hour_mapping) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        ("ds_20260115", "2026-01-15", "READY", json.dumps({"rows": 24}), "2026-01-14 14:00:00", 1),
    )
    cur.execute(
        "INSERT INTO efm_data_sources (source_id,source_name,source_type,market,enabled) VALUES (%s,%s,%s,%s,%s)",
        ("shandong_da", "Shandong DA", "directory", "shandong", 1),
    )
    cur.execute(
        "INSERT INTO efm_source_files (source_id,file_path,file_name,file_ext,file_sha256,import_status) VALUES (%s,%s,%s,%s,%s,%s)",
        ("shandong_da", "/data/da.csv", "da.csv", "csv", "sha256deadbeef", "IMPORTED"),
    )
    cur.execute(
        "INSERT INTO efm_feature_snapshots (run_id,target_date,hour_business,feature_hash) VALUES (%s,%s,%s,%s)",
        ("test_run_1", "2026-01-15", 12, "feat123"),
    )
    for stage, model, price, shadow, sel, reason in [
        ("da_anchor", "da_anchor", 300.0, 0, 0, None),
        ("official_baseline", "official_baseline", 310.0, 0, 0, None),
        ("seasonal_da_router", "seasonal_da_router", 305.0, 0, 1, "winter DA anchor"),
        ("selector_shadow", "selector_shadow", 299.0, 1, 0, None),
    ]:
        cur.execute(
            "INSERT INTO efm_predictions (run_id,target_date,hour_business,task,stage,model_name,model_version,pred_price,is_shadow,is_selected,selected_reason) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("test_run_1", "2026-01-15", 12, "final", stage, model, "v1", price, shadow, sel, reason),
        )
    cur.execute(
        "INSERT INTO efm_fusion_decisions (run_id,target_date,hour_business,policy_name,selected_model,decision_reason) VALUES (%s,%s,%s,%s,%s,%s)",
        ("test_run_1", "2026-01-15", 12, "seasonal_da_router", "seasonal_da_router", "winter"),
    )
    cur.execute(
        "INSERT INTO efm_postflight_checks (run_id,target_date,check_name,passed,details) VALUES (%s,%s,%s,%s,%s)",
        ("test_run_1", "2026-01-15", "row_count_24", 1, "ok"),
    )
    cur.execute(
        "INSERT INTO efm_delivery_outputs (run_id,target_date,output_type,output_path,row_count) VALUES (%s,%s,%s,%s,%s)",
        ("test_run_1", "2026-01-15", "submission_ready", "out/sub.csv", 24),
    )
    conn.commit()
    mgr.close()
    yield url
