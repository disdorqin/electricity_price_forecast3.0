"""Lineage API tests (DB-backed, env-gated) + schema validation."""

from fastapi.testclient import TestClient

from backend.app.schemas.reports import LineageResponse


def test_lineage_hour_schema(db_client: TestClient):
    r = db_client.get("/api/lineage/test_run_1/hour/12")
    assert r.status_code == 200
    body = r.json()
    # Validate against the response model (ensures schema stability)
    LineageResponse(**body)
    assert body["run_id"] == "test_run_1"
    assert body["hour_business"] == 12
    assert len(body["nodes"]) >= 4
    assert len(body["edges"]) >= 3
    assert body["router_decision"] is not None
    # the selected final must not be a shadow prediction
    assert body["shadow_safe"] is True


def test_lineage_run_summary(db_client: TestClient):
    r = db_client.get("/api/lineage/test_run_1")
    assert r.status_code == 200
    body = r.json()
    LineageResponse(**body)
    assert any(n["node_type"] == "router" for n in body["nodes"])


def test_lineage_hour_out_of_range(db_client: TestClient):
    r = db_client.get("/api/lineage/test_run_1/hour/99")
    assert r.status_code == 400


def test_lineage_not_found(db_client: TestClient):
    r = db_client.get("/api/lineage/missing_run/hour/12")
    assert r.status_code == 404
