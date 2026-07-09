"""SMAPE-sensitivity tests for the EFM3 metric-parity audit.

Pure-python unit checks (no DB) plus a DB-gated integration check that the
real 3.0 formal_sim metric is (a) ~49.7% for da_anchor vs rt_actual and
(b) strictly a DA-vs-RT spread (da_vs_da << da_vs_rt).

The integration test is skipped unless EFM3_DB_URL is set (mirrors the
env-gated db_client fixture used elsewhere in the repo).
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.db_ops.analyze_smape_contributors import (  # noqa: E402
    metrics_pooled,
    metrics_floor50_pooled,
)


def test_metrics_pooled_basic():
    pairs = [(100.0, 120.0), (200.0, 180.0)]
    m = metrics_pooled(pairs)
    assert m["smape"] == pytest.approx(14.3540, abs=1e-3)
    assert m["mae"] == pytest.approx(20.0, abs=1e-6)
    assert m["wmape"] == pytest.approx(13.3333, abs=1e-3)


def test_filtering_near_zero_actual_reduces_smape():
    # One extreme low-actual hour dominates; dropping it lowers SMAPE.
    pairs_all = [(474.0, 0.0), (300.0, 310.0), (280.0, 275.0)]
    pairs_filtered = [x for x in pairs_all if abs(x[1]) >= 1]
    sm_all = metrics_pooled(pairs_all)["smape"]
    sm_filt = metrics_pooled(pairs_filtered)["smape"]
    assert sm_filt < sm_all


def test_negative_actuals_are_counted():
    pairs = [(100.0, -59.67), (200.0, 50.0), (300.0, -80.0)]
    n_neg = sum(1 for _, a in pairs if a < 0)
    assert n_neg == 2
    # Negative actuals are valid inputs (no crash) and inflate SMAPE.
    m = metrics_pooled(pairs)
    assert m["smape"] > 0


def test_floor50_lowers_smape_for_zero_actual():
    pairs = [(474.0, 0.0)]
    raw = metrics_pooled(pairs)["smape"]          # 200%
    floor = metrics_floor50_pooled(pairs)["smape"]  # 162%
    assert floor < raw


@pytest.mark.skipif(
    not os.environ.get("EFM3_DB_URL"),
    reason="EFM3_DB_URL not set; skipping DB-backed SMAPE sensitivity check",
)
def test_real_3_0_da_vs_rt_is_da_vs_rt_spread():
    from tools.db_ops.analyze_smape_contributors import load_all_pairs  # noqa: E402
    from tools.db_ops.db_yearly_metrics import _connect  # noqa: E402

    conn = _connect(os.environ["EFM3_DB_URL"])
    cur = conn.cursor()
    data = load_all_pairs(cur, "2026-01-01", "2026-06-30")
    conn.close()

    rt = data["da_vs_rt"]
    da = data["da_vs_da"]
    assert len(rt) == 4344  # 181 days * 24h
    rt_smape = metrics_pooled(rt)["smape"]
    da_smape = metrics_pooled(da)["smape"]
    # The reported 49.70% is reproduced by this independent computation.
    assert rt_smape == pytest.approx(49.70, abs=0.5)
    # Same-product (da vs da) is far smaller -> the 49.70% is a cross-product
    # spread, not a model-error metric.
    assert da_smape < rt_smape
