"""Run read API tests (DB-backed, env-gated)."""

from fastapi.testclient import TestClient


def test_runs_list(db_client: TestClient):
    r = db_client.get("/api/runs")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list) and len(rows) >= 1
    assert any(x["run_id"] == "test_run_1" for x in rows)


def test_run_summary(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/summary")
    assert r.status_code == 200
    assert r.json()["run_id"] == "test_run_1"


def test_run_detail(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["prediction_counts"]["total"] == 4
    assert body["prediction_counts"]["selected"] == 1


def test_run_events_empty(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/events")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_run_postflight(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/postflight")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_run_delivery(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/delivery-outputs")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_run_not_found(db_client: TestClient):
    r = db_client.get("/api/runs/nonexistent_run")
    assert r.status_code == 404
