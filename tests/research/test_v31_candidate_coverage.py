"""
V3.1-R1 contract test — unified candidate coverage / evaluation support.

Per spec section 八, every candidate reports coverage_rows / coverage_ratio,
and the final ranking uses a single full-coverage common mask. Partial
candidates (per-season / 9-16-only) are excluded from that mask by design.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import v31_lib as L  # noqa: E402


def test_full_coverage_candidates_have_positive_coverage(mini_new):
    # Full-coverage (non-partial) candidates must all report positive coverage
    # and a usable ratio. Intrinsically-partial candidates (per-season / 9-16
    # only) are excluded from this mask by design and may be 0 in a mini window
    # that does not span their season.
    mdf = mini_new["mdf"]
    rank = set(mini_new["rank_cands"])
    sub = mdf[mdf["candidate"].isin(rank)]
    assert (sub["coverage_rows"] > 0).all()
    assert (sub["coverage_ratio"] > 0).all()


def test_b_midday_full_assembled(mini_new):
    res = mini_new["results"]
    assert ("expanding", "B_midday_full") in res
    full = res[("expanding", "B_midday_full")]
    assert np.nansum(~np.isnan(full)) > 0


def test_common_mask_has_rows(mini_new):
    assert mini_new["common_rows"] > 0


def test_full_coverage_ranking_candidates_are_reported(mini_new):
    # rank_cands excludes the intrinsically-PARTIAL set
    PARTIAL = {"C_winter", "C_summer", "C_shoulder", "B_midday", "B_midday_9_16_only"}
    rank = set(mini_new["rank_cands"])
    assert rank.isdisjoint(PARTIAL)
    assert len(rank) > 0
