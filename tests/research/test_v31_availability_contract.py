"""
V3.1-R1 contract test — forecast availability contract.

Enforces docs/research/V31_FORECAST_AVAILABILITY_CONTRACT.md:
  * the panel must NOT contain `legal_oos_da_prediction` (the V3.1 literal
    copy of da_actual) nor a stored `da_oos_pred` (legal proxy is built by
    the engine, never persisted in the panel).
  * `da_actual` / `rt_actual` remain as ACTUALS only.
  * RT features must not include any target-day `*_actual` column.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import v31_lib as L  # noqa: E402
from utils.business_day import (  # noqa: E402
    business_day_from_timestamp,
    hour_business_from_timestamp,
)


def test_panel_has_no_copied_da_prediction_column():
    df = L.load_panel()
    assert "legal_oos_da_prediction" not in df.columns
    assert "da_oos_pred" not in df.columns  # legal proxy built by engine, not stored


def test_panel_keeps_actuals_and_maps_business_day():
    df = L.load_panel()
    assert "da_actual" in df.columns
    assert "rt_actual" in df.columns
    assert "business_day" in df.columns and "hour_business" in df.columns
    mid = df[df["ds"].dt.hour == 0]
    for _, r in mid.iterrows():
        assert business_day_from_timestamp(r["ds"]) == (
            r["ds"] - pd.Timedelta(days=1)
        ).strftime("%Y-%m-%d")
        assert hour_business_from_timestamp(r["ds"]) == 24


def test_rt_features_exclude_target_day_actuals(prepared):
    _dfv, _yv, _hb, _month, feat_base, feat_anchor, _da = prepared
    leak = [c for c in (feat_base + feat_anchor)
            if c.endswith("_actual") and "_lag_" not in c]
    assert leak == [], f"RT features must not include target-day *_actual: {leak}"
