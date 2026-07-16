"""Shared fixtures for EFM3 V3.1-R1 research contract tests.

The heavy panel-prepare (build_oos_da + feature prep) runs ONCE per test
session and is cached, so the 8 contract modules don't each rebuild the
engine. Mini replays are also memoized.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "research"))

import v31_lib as L  # noqa: E402

# Candidate definitions (identical to tools/research/*_replay.py)
CALIB_DEFS = {
    "A05_med": ("l2", {}),
    "A05_q05": ("quantile", {"alpha": 0.05}),
    "A05_q95": ("quantile", {"alpha": 0.95}),
    "A05_huber": ("robust", {"objective": "huber", "alpha": 0.5}),
}
TRACK_DEFS = {
    "A_q05": ("quantile", {"alpha": 0.05}),
    "A_q50": ("quantile", {"alpha": 0.50}),
    "A_q95": ("quantile", {"alpha": 0.95}),
    "E_fair": ("robust", {"objective": "fair", "fair_c": 1.0}),
    "E_huber": ("robust", {"objective": "huber", "alpha": 0.5}),
    "D_anchor": ("anchor", {}),
    "B_midday": ("midday", {}),
    "C_winter": ("season", {"season": "winter"}),
    "C_summer": ("season", {"season": "summer"}),
    "C_shoulder": ("season", {"season": "shoulder"}),
    "F_regime": ("regime", {}),
}


@pytest.fixture(scope="session")
def prepared():
    """Full panel prepared once: (dfv, yv, hb, month_arr, feat_base, feat_anchor, da_oos_pred)."""
    df = L.load_panel()
    return L.prepare(df)


@pytest.fixture(scope="session")
def mini_new(prepared):
    return L.run_replay(TRACK_DEFS, out_prefix="FH_NEW_TEST", mini=True,
                        verbose=False, prepared=prepared)


@pytest.fixture(scope="session")
def mini_calib(prepared):
    return L.run_replay(CALIB_DEFS, out_prefix="FH_CALIB_TEST", mini=True,
                        verbose=False, prepared=prepared)
