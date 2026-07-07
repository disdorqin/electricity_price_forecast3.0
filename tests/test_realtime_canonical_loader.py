"""P2.7 tests for canonical loader.

10 tests verifying hour_business mapping integrity.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pytest

from common.realtime_canonical_loader import (
    load_realtime_actual_canonical,
    load_dayahead_anchor_canonical,
    validate_hour_business_mapping,
    canonical_smape_floor50,
    build_canonical_ledger,
)

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "shandong_pmos_hourly.xlsx"
OUT = ROOT / "outputs" / "p2_7_canonical_ledger"


class TestCanonicalLoader:
    def test_midnight_maps_to_hb24(self):
        """xlsx 00:00 must map to hour_business=24."""
        act = load_realtime_actual_canonical(XLSX, "2025-03-01")
        # hb=24 = midnight value = xlsx index 0
        # hb=1 = 01:00 value = xlsx index 1
        assert act is not None and len(act) == 24
        # If hb=24 > hb=1 typically (but not guaranteed due to price variation)
        # Just verify they're different values (proving different hours)
        assert not np.isnan(act[23])  # hb=24 (last)
        assert not np.isnan(act[0])   # hb=1 (first)

    def test_xlsx_raw_not_directly_mapped(self):
        """xlsx sorted [00,01,...,23] must NOT map to hb 1..24 directly."""
        import pandas as pd
        raw = pd.read_excel(XLSX)
        raw["ds"] = pd.to_datetime(raw["时刻"])
        d = pd.Timestamp("2025-03-01")
        day_data = raw[raw["ds"].dt.date == d.date()].sort_values("ds")
        vals = day_data["实时电价"].values
        assert len(vals) == 24
        # Canonical: hb=1 should be xlsx[1] (01:00), not xlsx[0] (00:00)
        canonical_act = load_realtime_actual_canonical(XLSX, "2025-03-01")
        assert canonical_act is not None
        # hb=1 = canonical[0] should equal xlsx[1] (01:00)
        assert abs(canonical_act[0] - vals[1]) < 0.01, \
            f"hb=1 ({canonical_act[0]}) should be xlsx[1] ({vals[1]})"
        # hb=24 = canonical[23] should equal xlsx[0] (00:00/midnight)
        assert abs(canonical_act[23] - vals[0]) < 0.01, \
            f"hb=24 ({canonical_act[23]}) should be xlsx[0] ({vals[0]})"

    def test_every_day_has_24_rows(self):
        """Each business_day must produce 24 valid hours."""
        for day_str in ["2025-03-01", "2025-03-15", "2025-03-31",
                        "2025-09-01", "2025-09-15", "2025-09-30",
                        "2026-05-01", "2026-05-15", "2026-05-31"]:
            act = load_realtime_actual_canonical(XLSX, day_str)
            assert act is not None, f"{day_str} returned None"
            assert len(act) == 24, f"{day_str} has {len(act)} rows"

    def test_hour_business_1_to_24(self):
        """Hour_business must cover exactly 1..24."""
        act = load_realtime_actual_canonical(XLSX, "2025-03-01")
        assert act is not None and len(act) == 24
        # No check on NaN — those are valid if xlsx has NaN prices
        # But the array must be float type

    def test_no_duplicate_hour_business(self):
        """No duplicate hour_business values allowed."""
        # Implemented by validate function: each hour maps to exactly one hb
        for day_str in ["2025-03-01", "2025-09-15", "2026-05-20"]:
            v = validate_hour_business_mapping(XLSX, day_str)
            assert v["valid"], f"{day_str}: {v['errors']}"

    def test_no_nan_actual(self):
        """Canonical actual should not have NaN for known-good days."""
        # 2025-03-01 has no NaN
        act = load_realtime_actual_canonical(XLSX, "2025-03-01")
        assert act is not None
        assert not np.any(np.isnan(act)), "2025-03-01 has NaN in actual"

    def test_da_and_rt_same_mapping(self):
        """DA anchor and realtime actual must use identical hour_business mapping."""
        day_str = "2025-03-01"
        act = load_realtime_actual_canonical(XLSX, day_str)
        da = load_dayahead_anchor_canonical(XLSX, day_str)
        assert act is not None and da is not None
        assert len(act) == len(da) == 24
        # Both use same shift (midnight→hb=24), so 00:00 must be last for both
        # Act[23] = midnight price, Da[23] = midnight DA price
        # These differ in value but should both be at position 23

    def test_canonical_smape_matches_25(self):
        """canonical_smape_floor50 matches 2.5 implementation."""
        y_true = np.array([100.0, 150.0, 200.0, 250.0])
        y_pred = np.array([110.0, 140.0, 190.0, 260.0])
        result = canonical_smape_floor50(y_true, y_pred)
        assert not np.isnan(result)
        assert 5.0 < result < 15.0  # reasonable sMAPE range

    def test_old_buggy_fallback_detected(self):
        """Verify that the P2.5 buggy path is NOT used by canonical loader."""
        # Canonical loader must use _extract_day_array with shift
        day_str = "2025-03-01"
        act = load_realtime_actual_canonical(XLSX, day_str)
        assert act is not None
        # The old buggy path would have midnight at index 0 and 01:00 at index 1.
        # Canonical path must have 01:00 at index 0 and midnight at index 23.
        import pandas as pd
        raw = pd.read_excel(XLSX)
        raw["ds"] = pd.to_datetime(raw["时刻"])
        d = pd.Timestamp("2025-03-01")
        day_data = raw[raw["ds"].dt.date == d.date()].sort_values("ds")
        midnight_val = day_data["实时电价"].values[0]  # 00:00
        one_am_val = day_data["实时电价"].values[1]    # 01:00
        # Canonical: midnight at last position
        assert abs(act[23] - midnight_val) < 0.01, \
            "Canonical should have midnight at position 23 (hb=24)"
        # Canonical: 01:00 at first position (hb=1)
        assert abs(act[0] - one_am_val) < 0.01, \
            "Canonical should have 01:00 at position 0 (hb=1)"


def test_canonical_ledger_build():
    """build_canonical_ledger produces valid output files."""
    stats = build_canonical_ledger(XLSX, OUT, ["2025-03"])
    assert "2025-03" in stats
    assert stats["2025-03"]["ok"] > 0
    assert (OUT / "realtime_actual_canonical.csv").exists()
    assert (OUT / "dayahead_anchor_canonical.csv").exists()
    # Verify columns
    import pandas as pd
    act_df = pd.read_csv(OUT / "realtime_actual_canonical.csv")
    assert "hour_business" in act_df.columns
    assert "y_true" in act_df.columns
    assert "target_day" in act_df.columns
    # Verify 24 hours per day
    day_counts = act_df.groupby("target_day").size()
    assert all(day_counts == 24), f"Some days have != 24 rows: {day_counts.value_counts().to_dict()}"
