"""Formula-parity tests: 3.0 SMAPE vs 2.5 floor-50 + aggregation.

Pure-python (no DB). Confirms:
  * 3.0 `compute_metrics` uses `200*|p-a|/(|p|+|a|)` (math-equivalent to 2.5 core).
  * 3.0 has NO floor-50 clipping (2.5 does) -> raw SMAPE saturates at 200% for a=0.
  * 3.0 aggregates daily-mean -> average; 2.5 pools all points. With unequal
    evaluable-hours-per-day these two operations diverge (3.0 weights each day
    equally; 2.5 weights each hour equally).
"""
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.db_ops.db_yearly_metrics import compute_metrics  # noqa: E402


def test_compute_metrics_core_formula():
    preds = {1: 100.0, 2: 200.0}
    actuals = {1: 120.0, 2: 180.0}
    m = compute_metrics(preds, actuals)
    # h1: 2*20/220*100 = 18.1818 ; h2: 2*20/380*100 = 10.5263 ; mean = 14.354
    assert m["smape"] == pytest.approx(14.3540, abs=1e-3)
    assert m["mae"] == pytest.approx(20.0, abs=1e-6)
    # WMAPE: sum(|err|)/sum(|actual|)*100 = 40/300*100 = 13.3333
    assert m["wmape"] == pytest.approx(13.3333, abs=1e-3)


def test_zero_denominator_is_recorded_zero():
    # 3.0 records smape=0 when pred==0 and actual==0 (no floor).
    m = compute_metrics({1: 0.0}, {1: 0.0})
    assert m["smape"] == 0.0
    assert m["skipped_smape"] == 0


def test_no_floor50_saturates_at_200_percent():
    # pred=474, actual=0 -> raw SMAPE = 200*|474|/474 = 200%
    m = compute_metrics({1: 474.0}, {1: 0.0})
    assert m["smape"] == pytest.approx(200.0, abs=1e-6)


def test_floor50_reduces_smape_for_low_actual():
    # Mirror 2.5 smape_floor50: max(p,50), max(a,50), then
    # mean(|pp-aa| / ((|pp|+|aa|)/2)) * 100
    p, a = 474.0, 0.0
    pp, aa = max(p, 50.0), max(a, 50.0)
    floor50 = abs(pp - aa) / ((abs(pp) + abs(aa)) / 2.0) * 100.0  # 161.83
    raw = compute_metrics({1: p}, {1: a})["smape"]  # 200.0
    assert floor50 < raw
    assert floor50 == pytest.approx(161.83, abs=0.05)


def test_daily_mean_then_avg_differs_from_pooled_when_hours_imbalanced():
    # Day A: 1 evaluable hour at 100% SMAPE. Day B: 23 evaluable hours at 0%.
    dayA_pred = {1: 100.0}
    dayA_act = {1: 300.0}                       # smape 100%
    dayB_pred = {h: 100.0 for h in range(2, 25)}
    dayB_act = {h: 100.0 for h in range(2, 25)}  # smape 0%

    sA = compute_metrics(dayA_pred, dayA_act)["smape"]
    sB = compute_metrics(dayB_pred, dayB_act)["smape"]
    daily_then_avg = (sA + sB) / 2.0            # 50%

    pooled = compute_metrics(
        {**dayA_pred, **dayB_pred},
        {**dayA_act, **dayB_act},
    )["smape"]                                   # 100/24 = 4.17%

    assert sA == pytest.approx(100.0)
    assert sB == pytest.approx(0.0)
    assert daily_then_avg == pytest.approx(50.0)
    assert pooled == pytest.approx(100.0 / 24.0, abs=1e-2)
    assert daily_then_avg != pytest.approx(pooled, abs=1e-2)
