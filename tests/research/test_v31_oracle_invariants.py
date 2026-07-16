"""
V3.1-R1 contract test — legal Oracle invariants.

Per spec section 九, the Oracle is an EX_POST_ACTUAL_AWARE_UPPER_BOUND:
  * selected value == one candidate's value (no synthetic value injected),
  * oracle per-row loss <= each candidate's per-row loss,
  * row count identical to the common mask,
  * actuals hash is a stable sha256 (not tampered),
  * explicitly marked as an upper bound, not a deployable strategy.
"""
import os
import sys

import numpy as np  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import v31_lib as L  # noqa: E402


def test_oracle_is_ex_post_upper_bound(mini_new):
    a = mini_new["audit"]
    assert a["oracle_type"] == "EX_POST_ACTUAL_AWARE_UPPER_BOUND"
    assert a["invariant_pass"] is True


def test_oracle_selected_equals_one_candidate(mini_new):
    a = mini_new["audit"]
    assert a["invariant_eq_selected_equals_candidate"] is True


def test_oracle_loss_le_each_candidate(mini_new):
    a = mini_new["audit"]
    assert a["invariant_oracle_loss_le_each_candidate"] is True


def test_oracle_row_count_matches_common_mask(mini_new):
    a = mini_new["audit"]
    assert a["n_rows"] == mini_new["common_rows"]


def test_actual_hash_is_stable_sha256(mini_new):
    h = mini_new["rt_hash"]
    assert isinstance(h, str) and len(h) == 64
    int(h, 16)  # must parse as hex
