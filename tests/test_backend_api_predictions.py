"""Prediction read API tests (DB-backed, env-gated)."""

from fastapi.testclient import TestClient


def test_predictions_list(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/predictions")
    assert r.status_code == 200
    assert len(r.json()) == 4


def test_predictions_hourly(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/predictions/hourly")
    assert r.status_code == 200
    assert all(x["hour_business"] == 12 for x in r.json())


def test_predictions_selected(db_client: TestClient):
    r = db_client.get("/api/runs/test_run_1/predictions/selected")
    assert r.status_code == 200
    sel = r.json()
    assert len(sel) == 1
    assert sel[0]["stage"] == "seasonal_da_router"


def test_predictions_compare(db_client: TestClient):
    r = db_client.get(
        "/api/runs/test_run_1/predictions/compare"
        "?models=da_anchor,official_baseline,seasonal_da_router"
    )
    assert r.status_code == 200
    stages = {x["stage"] for x in r.json()}
    assert stages == {"da_anchor", "official_baseline", "seasonal_da_router"}


def test_predictions_compare_schema_no_db(client: TestClient):
    # Without DB the endpoint degrades gracefully (503), proving the guard exists.
    r = client.get("/api/runs/test_run_1/predictions/compare?models=da_anchor")
    assert r.status_code == 503
