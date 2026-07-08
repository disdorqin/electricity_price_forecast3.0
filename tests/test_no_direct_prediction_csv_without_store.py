"""Storage contract — no pipeline may write a prediction CSV that bypasses the
PredictionStore / DB ledger. The exporter reads only selected-final rows.
"""

import csv
import re
from pathlib import Path

from common.prediction_store import FilePredictionStore

REPO = Path(__file__).resolve().parent.parent


def _read_src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_exporter_signature_requires_store():
    src = _read_src("pipelines/db_exporter.py")
    m = re.search(r"def export_submission_ready\(([^)]*)\)", src)
    assert m, "export_submission_ready not found in source"
    params = m.group(1)
    assert "prediction_store" in params, "exporter must take prediction_store"
    # The exporter must NOT read predictions from a raw CSV path directly.
    assert "csv_path" not in params
    assert "predictions_csv" not in params


def test_exporter_uses_selected_not_shadow(tmp_path):
    store = FilePredictionStore(base_dir=str(tmp_path))
    run_id, td = "run_y", "2026-02-20"
    store.write_selected_final(
        run_id, td,
        [{"hour_business": 5, "pred_price": 321.0, "policy_name": "p",
          "selected_model": "m", "decision_reason": "r"}],
    )
    store.write_shadow_predictions(
        run_id, td, "extreme_price_shadow",
        [{"hour_business": 5, "task": "shadow", "stage": "extreme_price_shadow",
          "model_name": "x", "model_version": "v", "pred_price": 777.0,
          "is_shadow": True, "is_selected": False, "selected_reason": None,
          "quality_flags": None}],
    )
    out = tmp_path / "sub.csv"
    store.export_submission_ready(run_id, td, str(out))
    rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
    h5 = next(r for r in rows if int(r["hour_business"]) == 5)
    assert h5["realtime_price"] == "321.0000"
    assert h5["realtime_price"] != "777.0000", "shadow price leaked into export"


def test_orchestrator_export_passes_store():
    src = _read_src("pipelines/full_chain_orchestrator.py")
    assert "export_submission_ready(" in src
    assert "prediction_store=store" in src, "orchestrator must forward the store to the exporter"


def test_no_new_pipeline_writes_csv_without_store():
    # Regression guard: the canonical chain always materialises a PredictionStore.
    src = _read_src("pipelines/full_chain_orchestrator.py")
    assert ("MySQLPredictionStore(" in src) or ("FilePredictionStore(" in src)
