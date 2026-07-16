"""
V3.1-R1 contract test — business-time mapping.

Central rule: hour 24 of business_day D == timestamp D+1 00:00:00.
This replaces the buggy V3.1 `business_day = times.date` mapping that
produced hour-0 / shifted-day rows.
"""
import os
import sys

import numpy as np  # noqa: F401  (kept for parity with other test modules)
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.business_day import (  # noqa: E402
    business_day_from_timestamp,
    hour_business_from_timestamp,
    timestamp_from_business,
    infer_period,
)


def test_d_plus_1_midnight_maps_to_prev_business_day_hour24():
    ts = pd.Timestamp("2022-01-02 00:00:00")
    assert business_day_from_timestamp(ts) == "2022-01-01"
    assert hour_business_from_timestamp(ts) == 24


def test_normal_hour_maps_to_same_day():
    ts = pd.Timestamp("2022-01-01 14:00:00")
    assert business_day_from_timestamp(ts) == "2022-01-01"
    assert hour_business_from_timestamp(ts) == 14


def test_hour0_with_minutes_is_not_hour24():
    # 00:30 is not the midnight boundary -> business_day = that day, hour 0
    ts = pd.Timestamp("2022-03-05 00:30:00")
    assert business_day_from_timestamp(ts) == "2022-03-05"
    assert hour_business_from_timestamp(ts) == 0


def test_round_trip_business_to_timestamp():
    cases = [
        ("2022-01-01", 24, pd.Timestamp("2022-01-02 00:00:00")),
        ("2022-01-01", 1, pd.Timestamp("2022-01-01 01:00:00")),
        ("2022-06-15", 17, pd.Timestamp("2022-06-15 17:00:00")),
    ]
    for bd, hb, exp in cases:
        got = timestamp_from_business(bd, hb)
        assert got == exp, f"{bd}/{hb} -> {got} != {exp}"
        # and back
        assert business_day_from_timestamp(exp) == bd
        assert hour_business_from_timestamp(exp) == hb


def test_period_classification():
    assert infer_period(1) == "1_8"
    assert infer_period(8) == "1_8"
    assert infer_period(9) == "9_16"
    assert infer_period(16) == "9_16"
    assert infer_period(17) == "17_24"
    assert infer_period(24) == "17_24"


def test_mapping_is_deterministic_across_year_boundary():
    ts = pd.Timestamp("2023-01-01 00:00:00")
    assert business_day_from_timestamp(ts) == "2022-12-31"
    assert hour_business_from_timestamp(ts) == 24
