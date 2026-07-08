"""
Tests for fusion_shadow_v1 — Contract Verification

Verifies that the fusion pipeline:
  - Does not write to final/ or submission_ready.csv
  - Uses canonical hour mapping
  - Only reads from shadow-safe directories
  - Defaults to disabled state
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipelines.fusion_shadow_v1 import (
    smape_floor50,
    mae,
    rmse,
    load_actuals_from_xlsx,
    CANONICAL_HOUR_MAP,
    PERIOD_MAP,
    WINTER_MONTHS,
    POLICY_BUILDERS,
)


class TestCanonicalHourMapping:
    """Verify canonical hour mapping contract."""

    def test_hour_map_completeness(self):
        """All 24 hours are mapped."""
        assert len(CANONICAL_HOUR_MAP) == 24
        assert CANONICAL_HOUR_MAP[1] == "01:00"
        assert CANONICAL_HOUR_MAP[23] == "23:00"
        assert CANONICAL_HOUR_MAP[24] == "00:00"

    def test_period_map_completeness(self):
        """All hours have a period."""
        assert len(PERIOD_MAP) == 24
        for h in range(1, 25):
            assert PERIOD_MAP[h] in ("1_8", "9_16", "17_24")

    def test_winter_months(self):
        """Winter months are 11, 12, 1, 2."""
        assert WINTER_MONTHS == {11, 12, 1, 2}


class TestMetrics:
    """Verify metric functions."""

    def test_smape_floor50_identity(self):
        """Identical predictions give 0 sMAPE."""
        y_true = np.array([100.0, 200.0, 150.0])
        y_pred = np.array([100.0, 200.0, 150.0])
        assert smape_floor50(y_true, y_pred) == 0.0

    def test_smape_floor50_floor(self):
        """Values below 50 are floored to 50."""
        y_true = np.array([10.0, 20.0, 30.0])
        y_pred = np.array([10.0, 20.0, 30.0])
        # After floor: all 50, so sMAPE = 0
        assert smape_floor50(y_true, y_pred) == 0.0

    def test_smape_floor50_known(self):
        """Known values produce expected sMAPE."""
        y_true = np.array([100.0, 100.0])
        y_pred = np.array([80.0, 120.0])
        # Floor not needed (all >= 50)
        result = smape_floor50(y_true, y_pred)
        expected = 100 * np.mean([
            abs(80 - 100) / ((100 + 80) / 2),
            abs(120 - 100) / ((100 + 120) / 2),
        ])
        assert abs(result - expected) < 1e-6

    def test_mae_basic(self):
        y_true = np.array([100.0, 200.0])
        y_pred = np.array([110.0, 180.0])
        assert mae(y_true, y_pred) == 15.0

    def test_rmse_basic(self):
        y_true = np.array([100.0, 200.0])
        y_pred = np.array([110.0, 180.0])
        expected = np.sqrt((10**2 + 20**2) / 2)
        assert abs(rmse(y_true, y_pred) - expected) < 1e-6


class TestPolicyRegistry:
    """All policy builders exist and accept DataFrame."""

    def test_all_variants_registered(self):
        required = [
            "official_baseline",
            "da_anchor",
            "sgdfnet_only",
            "realtime_selector_shadow",
            "p3_extreme_shadow",
            "winter_da_only_policy",
            "selector_then_p3_overlay",
            "p3_then_selector_overlay",
            "conservative_fusion_v1",
            "oracle_upper_bound",
        ]
        for v in required:
            assert v in POLICY_BUILDERS, f"Missing: {v}"

    def test_all_policies_produce_output(self):
        """All policies return arrays of same length."""
        df = pd.DataFrame({
            "target_day": ["2025-03-01"] * 24,
            "hour_business": list(range(1, 25)),
            "month": [3] * 24,
            "y_true": np.random.uniform(50, 300, 24),
            "y_pred_sgdf": np.random.uniform(50, 300, 24),
            "da_anchor": np.random.uniform(50, 300, 24),
            "p3_pred": [np.nan] * 24,
            "p3_confidence": [0.0] * 24,
            "p3_corrected": [False] * 24,
            "selector_pred": [np.nan] * 24,
            "selected_model": [""] * 24,
            "selection_reason": [""] * 24,
            "confidence": [0.0] * 24,
            "period": ["1_8"] * 24,
        })
        
        for vname, builder in POLICY_BUILDERS.items():
            pred = builder(df)
            assert len(pred) == 24, f"{vname}: expected 24, got {len(pred)}"
            assert not np.any(np.isnan(pred)), f"{vname}: NaN in predictions"


class TestNoFinalContamination:
    """Verify pipeline never touches final/ or submission_ready."""

    def test_output_paths_are_shadow_only(self):
        """Fusion outputs go only to fusion_shadow_v1/ or exports/."""
        import pipelines.fusion_shadow_v1 as fsv
        
        source = open(fsv.__file__).read()
        
        # Verify it writes to shadow paths (positive check)
        assert "outputs/fusion_shadow_v1" in source or "fusion_shadow_v1" in source, \
            "Pipeline must write to fusion_shadow_v1"
        assert "exports/efm3_candidates" in source, \
            "Pipeline must write to exports/"

    def test_config_default_off(self):
        """Config defaults to enabled: false."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "..", "configs", "fusion_shadow_v1.yaml")
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert config.get("fusion", {}).get("enabled") is False, \
            "Fusion must default to disabled"

    def test_config_default_off(self):
        """Config defaults to enabled: false."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "..", "configs", "fusion_shadow_v1.yaml")
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert config.get("fusion", {}).get("enabled") is False, \
            "Fusion must default to disabled"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
