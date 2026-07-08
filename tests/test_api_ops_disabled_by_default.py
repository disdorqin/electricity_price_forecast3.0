"""Ops endpoints must be disabled by default (EFM3_OPS_ENABLED=false).

When disabled, EVERY /api/ops/* request returns 403 — regardless of origin
(no localhost bypass). This is the core safety guarantee: ops trigger real
pipeline side effects and must never run silently.
"""

from fastapi.testclient import TestClient

from backend.app.config import settings


def test_ops_disabled_by_default(client: TestClient):
    # Default config has ops_enabled=False.
    assert settings.ops_enabled is False
    for path in [
        "/api/ops/init-db",
        "/api/ops/update-data",
        "/api/ops/run-dry-run",
        "/api/ops/run-shadow-monitoring",
        "/api/ops/export-submission",
        "/api/ops/run-formal",
    ]:
        r = client.post(path, json={"target_date": "2026-01-01", "confirm": True, "reason": "x"})
        assert r.status_code == 403, f"{path} should be 403 when ops disabled, got {r.status_code}"


def test_ops_disabled_even_from_localhost(client: TestClient):
    # Even a localhost caller must get 403 when ops are disabled.
    r = client.post(
        "/api/ops/init-db",
        json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()
