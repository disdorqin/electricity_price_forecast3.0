"""
V3.1-R1 contract test — Track F (no-leakage two-stage residual).

Defect #5: V3.1 fed the residual *as an input* and used the same rows for
train and inference (leakage). The fix is a strict OOF two-stage:
  stage1: K-fold OOF base model -> out-of-fold residual = y - oof
  stage2: predict residual from LEGAL features only
  final  = base_pred + resid_pred

This test asserts coverage, that F is NOT a copy of da_actual (no target-day
leakage), and that the residual stage actually changed the base prediction.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import v31_lib as L  # noqa: E402


def test_f_regime_has_coverage(mini_new):
    res = mini_new["results"]
    f = res[("expanding", "F_regime")]
    assert np.nansum(~np.isnan(f)) > 0


def test_f_regime_not_a_da_actual_copy(mini_new, prepared):
    res = mini_new["results"]
    f = res[("expanding", "F_regime")]
    # mini_new's arrays are aligned to the mini-sliced dfv (first 30 business
    # days). Reconstruct that same slice from the full prepared dfv so da aligns.
    dfv_full = prepared[0]
    d30 = sorted(dfv_full["business_day"].unique())[:30]
    m30 = dfv_full["business_day"].isin(d30).values
    da = dfv_full[m30]["da_actual"].astype(float).values
    m = ~np.isnan(f)
    corr = np.corrcoef(f[m], da[m])[0, 1]
    assert corr < 0.999, f"F_regime suspiciously equals da_actual (corr={corr:.4f})"


def test_f_regime_residual_correction_active(mini_new):
    res = mini_new["results"]
    f = res[("expanding", "F_regime")]
    base = res[("expanding", "A_q50")]  # a plain quantile base on the same features
    m = ~np.isnan(f) & ~np.isnan(base)
    diff = np.abs(f[m] - base[m])
    assert diff.max() > 1e-6, "residual stage did not change the base prediction"
