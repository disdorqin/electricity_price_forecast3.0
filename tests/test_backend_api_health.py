"""Health API tests (no DB required)."""

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "efm3-control-plane"


def test_health_db_not_configured(client: TestClient):
    r = client.get("/api/health/db")
    assert r.status_code == 200
    assert r.json()["status"] in ("not_configured", "ok", "error")


def test_health_schema_no_db(client: TestClient):
    r = client.get("/api/health/schema")
    assert r.status_code == 200
    assert r.json()["status"] in ("not_configured", "no_db", "ok", "error")
