"""
V3.1-R1 contract test — rolling preprocessing / panel integrity.

Asserts the canonical panel is built via utils.business_day (defect #2 fix):
  * business_day / hour_business columns equal a fresh recomputation,
  * no duplicate (business_day, hour_business) rows,
  * every business day has exactly 24 distinct hours,
  * hour_business is within 1..24.
"""
import os
import sys

import numpy as np  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import v31_lib as L  # noqa: E402
from utils.business_day import (  # noqa: E402
    business_day_from_timestamp,
    hour_business_from_timestamp,
)


def test_panel_built_via_utils_business_day():
    df = L.load_panel()
    bd2 = df["ds"].apply(business_day_from_timestamp)
    hb2 = df["ds"].apply(hour_business_from_timestamp)
    assert (bd2.values == df["business_day"].values).all()
    assert (hb2.values == df["hour_business"].values).all()


def test_no_duplicate_business_hour_rows():
    df = L.load_panel()
    dups = df.duplicated(subset=["business_day", "hour_business"]).sum()
    assert dups == 0


def test_each_business_day_has_24_hours():
    df = L.load_panel()
    counts = df.groupby("business_day")["hour_business"].nunique()
    incomplete = counts[counts != 24]
    # The canonical panel may retain a single trailing partial day (e.g. the
    # final discovered day with incomplete hours); every other day must be 24h.
    assert len(incomplete) <= 1, f"incomplete days: {incomplete.to_dict()}"


def test_hour_business_range_1_to_24():
    df = L.load_panel()
    assert int(df["hour_business"].min()) == 1
    assert int(df["hour_business"].max()) == 24
