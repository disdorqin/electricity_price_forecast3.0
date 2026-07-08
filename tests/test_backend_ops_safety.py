"""Ops safety tests: confirm guard, API-key/local guard, no arbitrary command.

None of these ever trigger a real pipeline (DB is not configured in this client),
so they verify the *guards* without side effects.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.utils.subprocess_runner import ALLOWED_ACTIONS, DANGEROUS_ACTIONS, build_ops_command
from backend.app.services import ops_service


def _with_ops_enabled():
    """Temporarily enable ops so confirm/reason guards (not the disabled 403) are tested."""
    saved = settings.ops_enabled
    settings.ops_enabled = True
    return saved


def test_export_submission_requires_confirm(client: TestClient):
    saved = _with_ops_enabled()
    try:
        r = client.post("/api/ops/export-submission", json={"target_date": "2026-01-01", "confirm": False})
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()
    finally:
        settings.ops_enabled = saved


def test_formal_requires_confirm(client: TestClient):
    saved = _with_ops_enabled()
    try:
        r = client.post("/api/ops/run-formal", json={"target_date": "2026-01-01", "confirm": False})
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()
    finally:
        settings.ops_enabled = saved


def test_confirm_true_passes_guard(client: TestClient):
    # With confirm=true AND a reason the guard passes; it then fails only on
    # missing DB (503), proving confirm+reason are honored (not silently blocked).
    saved = _with_ops_enabled()
    try:
        r = client.post(
            "/api/ops/export-submission",
            json={"target_date": "2026-01-01", "confirm": True, "reason": "scheduled export"},
        )
        assert r.status_code == 503  # DB not configured, not a confirm/reason error
    finally:
        settings.ops_enabled = saved


def test_api_key_guard():
    original_key = settings.api_key
    original_allow = settings.ops_allow_from
    settings.api_key = "secret-token"
    settings.ops_allow_from = ""  # force non-local so the key is required
    try:
        # rebuild client settings binding
        from backend.app.main import app
        from fastapi.testclient import TestClient as TC

        c = TC(app)
        assert c.get("/api/health").status_code == 401
        assert c.get("/api/health", headers={"X-API-Key": "secret-token"}).status_code == 200
        assert c.get("/api/health", headers={"X-API-Key": "wrong"}).status_code == 401
    finally:
        settings.api_key = original_key
        settings.ops_allow_from = original_allow


def test_no_arbitrary_command_allowed():
    # The action set is closed and contains no shell escape.
    assert ALLOWED_ACTIONS == {
        "init-db", "update-data", "run-dry-run",
        "run-shadow-monitoring", "export-submission", "run-formal",
    }
    assert DANGEROUS_ACTIONS == {"export-submission", "run-formal"}

    # Unknown action must be rejected (no arbitrary command execution).
    with pytest.raises(ValueError):
        build_ops_command("rm -rf /", {"target_date": "2026-01-01"}, db_url="x")

    # Every allowed action builds a fixed argv (no shell metacharacters).
    for action in ALLOWED_ACTIONS:
        params = {"target_date": "2026-01-01"} if action != "init-db" else {}
        argv = build_ops_command(action, params, db_url="mysql+pymysql://u:p@h:3306/d")
        # argv must be a list and never contain a shell operator
        assert isinstance(argv, list)
        joined = " ".join(argv)
        assert ";" not in joined and "|" not in joined and "&&" not in joined


def test_ops_service_rejects_illegal_action():
    with pytest.raises(ops_service.OpsError):
        ops_service.run_action("DROP TABLE efm_runs", {}, client_host="127.0.0.1")
