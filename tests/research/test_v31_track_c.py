"""
V3.1-R1 contract test — Track C (seasonal index fix).

Defect #6: the relative season mask was applied to absolute train_idx, causing
empty / wrong seasonal windows. The fix re-indexes to absolute and assembles
C_seasonal_full as the union of the three per-season predictions (each row
belongs to exactly one season).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import v31_lib as L  # noqa: E402


def test_c_seasonal_full_assembled(mini_new):
    res = mini_new["results"]
    assert ("expanding", "C_seasonal_full") in res
    full = res[("expanding", "C_seasonal_full")]
    assert np.nansum(~np.isnan(full)) > 0


def test_c_season_at_least_one_part_present(mini_new):
    # In a mini window only the seasons present in the slice get non-zero
    # coverage (e.g. an April-May slice is shoulder-only). The contract is that
    # at least one seasonal part is populated and C_seasonal_full is assembled.
    res = mini_new["results"]
    cov = {}
    for name in ("C_winter", "C_summer", "C_shoulder"):
        if ("expanding", name) in res:
            cov[name] = int(np.nansum(~np.isnan(res[("expanding", name)])))
    assert sum(cov.values()) > 0


def test_c_seasonal_full_is_union_of_parts(mini_new):
    res = mini_new["results"]
    full = res[("expanding", "C_seasonal_full")]
    parts = np.full(len(full), np.nan)
    for name in ("C_winter", "C_summer", "C_shoulder"):
        p = res[("expanding", name)]
        parts[~np.isnan(p)] = p[~np.isnan(p)]
    mask = ~np.isnan(full)
    # every filled full-row equals the union value
    assert np.allclose(full[mask], parts[mask], equal_nan=True)
    # full has no row outside the union of the three parts
    assert np.isnan(full[np.isnan(parts)]).all()
