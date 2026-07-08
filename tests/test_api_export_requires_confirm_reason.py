"""Dangerous ops (export-submission / run-formal) require both confirm=true
AND a non-empty reason.

These tests enable ops (ops_enabled=True) so the disabled-403 guard is not the
thing under test; they verify the confirm+reason gate specifically.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings


@pytest.fixture
def ops_client(client: TestClient):
    saved = settings.ops_enabled
    settings.ops_enabled = True
    yield client
    settings.ops_enabled = saved


def test_export_requires_confirm(ops_client: TestClient):
    r = ops_client.post("/api/ops/export-submission", json={"target_date": "2026-01-01", "confirm": False})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def test_export_requires_reason(ops_client: TestClient):
    r = ops_client.post(
        "/api/ops/export-submission",
        json={"target_date": "2026-01-01", "confirm": True, "reason": ""},
    )
    assert r.status_code == 400
    assert "reason" in r.json()["detail"].lower()


def test_export_confirm_and_reason_pass_guard(ops_client: TestClient):
    # With both confirm and reason, the guard passes; it then fails only on the
    # missing DB (503), proving the gate is honored (not silently blocked).
    r = ops_client.post(
        "/api/ops/export-submission",
        json={"target_date": "2026-01-01", "confirm": True, "reason": "scheduled export"},
    )
    assert r.status_code == 503  # DB not configured, not a confirm/reason error


def test_formal_requires_confirm_and_reason(ops_client: TestClient):
    r = ops_client.post(
        "/api/ops/run-formal",
        json={"target_date": "2026-01-01", "confirm": True},
    )
    assert r.status_code == 400
    assert "reason" in r.json()["detail"].lower()

    r2 = ops_client.post(
        "/api/ops/run-formal",
        json={"target_date": "2026-01-01", "confirm": False, "reason": "deploy"},
    )
    assert r2.status_code == 400
    assert "confirm" in r2.json()["detail"].lower()
