"""Storage contract — every prediction path must go through the PredictionStore.

MySQL is the MAIN ledger. CSV is only an export artifact. Shadow outputs are
marked is_shadow=true and are NEVER selected into the final delivery.
"""

import csv
import re
from pathlib import Path

from common.prediction_store import (
    PredictionStore,
    MySQLPredictionStore,
    FilePredictionStore,
    create_prediction_store,
)

REPO = Path(__file__).resolve().parent.parent


def _read_src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_create_prediction_store_mysql_when_url():
    s = create_prediction_store(db_url="mysql+pymysql://u:p@h:3306/d")
    assert isinstance(s, PredictionStore)
    assert isinstance(s, MySQLPredictionStore)


def test_create_prediction_store_file_when_no_url():
    s = create_prediction_store()
    assert isinstance(s, PredictionStore)
    assert isinstance(s, FilePredictionStore)


def test_both_stores_are_prediction_store():
    assert issubclass(MySQLPredictionStore, PredictionStore)
    assert issubclass(FilePredictionStore, PredictionStore)


def test_seasonal_router_accepts_prediction_store():
    src = _read_src("pipelines/seasonal_da_router.py")
    m = re.search(r"def run_seasonal_da_router\(([^)]*)\)", src)
    assert m, "run_seasonal_da_router not found in source"
    assert "prediction_store" in m.group(1), "seasonal router must accept prediction_store"


def test_orchestrator_creates_a_store():
    src = _read_src("pipelines/full_chain_orchestrator.py")
    assert ("MySQLPredictionStore(" in src) or ("FilePredictionStore(" in src)
    assert "store.write_predictions" in src, "orchestrator must write predictions via the store"


def test_shadow_never_selected(tmp_path):
    store = FilePredictionStore(base_dir=str(tmp_path))
    run_id, td = "run_x", "2026-01-15"
    # Final-selected price for hour 1.
    store.write_selected_final(
        run_id, td,
        [{"hour_business": 1, "pred_price": 400.0, "policy_name": "p",
          "selected_model": "m", "decision_reason": "r"}],
    )
    # A shadow prediction for the SAME hour — must never be selected.
    store.write_shadow_predictions(
        run_id, td, "selector_shadow",
        [{"hour_business": 1, "task": "shadow", "stage": "selector_shadow",
          "model_name": "x", "model_version": "v", "pred_price": 999.0,
          "is_shadow": True, "is_selected": False, "selected_reason": None,
          "quality_flags": None}],
    )
    out = tmp_path / "sub.csv"
    store.export_submission_ready(run_id, td, str(out))
    rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
    h1 = next(r for r in rows if int(r["hour_business"]) == 1)
    assert h1["realtime_price"] == "400.0000", h1
    assert h1["realtime_price"] != "999.0000", "shadow price leaked into final"
