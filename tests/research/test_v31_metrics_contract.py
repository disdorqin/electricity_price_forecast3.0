"""
V3.1-R1 contract test — unified metrics (tools/research/metrics_contract.py).

Asserts the single canonical plain_smape / smape_floor50 entry points match
the exact specification formulas and handle the defect-#3 cases
(perfect prediction -> 0; negative / zero prices finite; symmetry).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "research"))

from metrics_contract import plain_smape, smape_floor50  # noqa: E402


def _ref_plain(a, p):
    a = np.asarray(a, float)
    p = np.asarray(p, float)
    return float(np.mean(np.abs(a - p) / ((np.abs(a) + np.abs(p)) / 2.0)) * 100)


def _ref_f50(a, p, floor=50.0):
    a = np.asarray(a, float)
    p = np.asarray(p, float)
    fa = np.where(np.abs(a) < floor, np.sign(a) * floor, a)
    fp = np.where(np.abs(p) < floor, np.sign(p) * floor, p)
    denom = (np.abs(fa) + np.abs(fp)) / 2.0
    return float(np.mean(np.abs(fp - fa) / denom) * 100)


def test_plain_smape_matches_reference():
    rng = np.random.default_rng(1)
    a = rng.uniform(-200, 500, 1000)
    p = rng.uniform(-200, 500, 1000)
    assert abs(plain_smape(a, p) - _ref_plain(a, p)) < 1e-9


def test_floor50_matches_reference():
    rng = np.random.default_rng(2)
    a = rng.uniform(-200, 500, 1000)
    p = rng.uniform(-200, 500, 1000)
    assert abs(smape_floor50(a, p) - _ref_f50(a, p)) < 1e-9


def test_perfect_prediction_is_zero():
    a = np.array([1.0, -2.0, 100.0, -50.0])
    assert abs(plain_smape(a, a)) < 1e-9


def test_negative_and_zero_prices_finite():
    a = np.array([-50.0, 10.0, -5.0, 0.0])
    p = np.array([-40.0, 12.0, 0.0, 1.0])
    assert np.isfinite(plain_smape(a, p))
    assert np.isfinite(smape_floor50(a, p))


def test_plain_smape_symmetric():
    rng = np.random.default_rng(3)
    a = rng.uniform(-100, 100, 200)
    p = rng.uniform(-100, 100, 200)
    assert abs(plain_smape(a, p) - plain_smape(p, a)) < 1e-9


def test_floor50_equals_plain_when_all_above_floor():
    # When every |value| >= floor, clipping is a no-op -> identical to plain.
    a = np.array([100.0, -200.0, 300.0])
    p = np.array([120.0, -180.0, 250.0])
    assert abs(smape_floor50(a, p) - plain_smape(a, p)) < 1e-9


def test_floor50_finite_and_not_greater_than_plain_on_low_prices():
    # Clipping raises the denominator, so floor50 <= plain (never amplifies);
    # the key contract is that it stays finite on low / negative prices.
    a = np.array([10.0, -20.0, 5.0])
    p = np.array([15.0, -25.0, 8.0])
    assert np.isfinite(smape_floor50(a, p))
    assert smape_floor50(a, p) <= plain_smape(a, p) + 1e-9
