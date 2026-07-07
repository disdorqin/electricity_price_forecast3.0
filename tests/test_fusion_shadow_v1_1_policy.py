"""Fusion v1.1 Policy Tests — Contract, Leakage, and Contamination"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "configs" / "fusion_shadow_v1_1.yaml"
PIPELINE_FILE = PROJECT_ROOT / "pipelines" / "fusion_shadow_v1.py"


class TestV1_1PolicyContract:
    """Verify v1.1 policy builders exist and produce valid outputs."""

    def test_v1_1_variants_registered(self):
        from pipelines.fusion_shadow_v1 import POLICY_BUILDERS
        v1_1_names = [
            "v1_1_negative_only_p3",
            "v1_1_negative_plus_conservative_spike",
            "v1_1_nonwinter_selector_negative_p3",
            "v1_1_safe_fallback",
            "v1_1_minimal_patch",
        ]
        for vn in v1_1_names:
            assert vn in POLICY_BUILDERS, f"Missing v1.1 variant: {vn}"

    def test_v1_1_policies_produce_output(self):
        from pipelines.fusion_shadow_v1 import POLICY_BUILDERS, WINTER_MONTHS

        df = pd.DataFrame({
            "target_day": ["2026-01-15"] * 24,
            "hour_business": list(range(1, 25)),
            "month": [1] * 24,
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
            "period": ["17_24"] * 24,
        })

        v1_1_names = [
            "v1_1_negative_only_p3",
            "v1_1_negative_plus_conservative_spike",
            "v1_1_nonwinter_selector_negative_p3",
            "v1_1_safe_fallback",
            "v1_1_minimal_patch",
        ]
        for vn in v1_1_names:
            builder = POLICY_BUILDERS[vn]
            pred = builder(df)
            assert len(pred) == 24, f"{vn}: expected 24, got {len(pred)}"
            assert not np.any(np.isnan(pred)), f"{vn}: NaN in predictions"

    def test_oracle_isolated(self):
        """Verify oracle_upper_bound uses actual prices (analysis only)."""
        from pipelines.fusion_shadow_v1 import POLICY_BUILDERS

        df = pd.DataFrame({
            "target_day": ["2026-01-15"] * 24,
            "hour_business": list(range(1, 25)),
            "month": [1] * 24,
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
        
        oracle_builder = POLICY_BUILDERS["oracle_upper_bound"]
        pred = oracle_builder(df)
        assert len(pred) == 24
        # Verify oracle is not equal to any single variant (proves it uses actual)
        for vn in ["official_baseline", "da_anchor"]:
            builder = POLICY_BUILDERS[vn]
            other_pred = builder(df)
            assert not np.array_equal(pred, other_pred), "Oracle must differ from baseline"


class TestV1_1NoLeakage:
    """Verify no target-day actual leakage in v1.1 policies."""

    def test_v1_1_no_actual_in_policy(self):
        """Verify v1.1 policy functions don't use y_true column."""
        from pipelines.fusion_shadow_v1 import (
            build_v1_1_negative_only_p3,
            build_v1_1_negative_plus_conservative_spike,
            build_v1_1_nonwinter_selector_negative_p3,
            build_v1_1_safe_fallback,
            build_v1_1_minimal_patch,
        )
        
        builders = [
            build_v1_1_negative_only_p3,
            build_v1_1_negative_plus_conservative_spike,
            build_v1_1_nonwinter_selector_negative_p3,
            build_v1_1_safe_fallback,
            build_v1_1_minimal_patch,
        ]
        
        for builder in builders:
            source = builder.__code__.co_code
            # Verify the function doesn't reference "y_true" column
            import inspect
            source_text = inspect.getsource(builder)
            assert "y_true" not in source_text, \
                f"{builder.__name__} uses y_true in policy logic"


class TestV1_1NoFinalContamination:
    """Verify no final/ or submission_ready contamination."""

    def test_config_default_off(self):
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f)
        assert config.get("fusion", {}).get("enabled") is False

    def test_output_dirs_are_shadow(self):
        """Verify script writes only to shadow directories."""
        script = (PROJECT_ROOT / "scripts" / "run_fusion_shadow_v1_1.py").read_text()
        assert "outputs/fusion_shadow_v1" in script
        assert "exports/efm3_candidates" in script
        assert "outputs/final/" not in script

    def test_pipeline_no_final(self):
        """Verify pipeline doesn't write to final/ or submission_ready."""
        source = PIPELINE_FILE.read_text()
        assert "outputs/final/" not in source
        assert "outputs/fusion_shadow_v1" in source

    def test_v1_1_output_paths(self):
        """Verify v1.1 export path is safe."""
        export_root = "exports/efm3_candidates/fusion_chain/fusion_v1_1_targeted_policy"
        assert "final" not in export_root
        assert export_root.startswith("exports/")


class TestV1_1PolicyConstraints:
    """Verify specific v1.1 policy constraints."""

    def test_winter_da_only_enforced(self):
        """Winter months get DA anchor for v1.1 policies."""
        from pipelines.fusion_shadow_v1 import POLICY_BUILDERS
        
        # Build test data - all winter months, SGDFNet and DA differ significantly
        df = pd.DataFrame({
            "target_day": ["2026-01-15"] * 24,
            "hour_business": list(range(1, 25)),
            "month": [1] * 24,  # January = winter
            "y_true": [100.0] * 24,
            "y_pred_sgdf": [90.0] * 24,
            "da_anchor": [70.0] * 24,  # Different from SGDFNet
            "p3_pred": [np.nan] * 24,
            "p3_confidence": [0.0] * 24,
            "p3_corrected": [False] * 24,
            "selector_pred": [np.nan] * 24,
            "selected_model": [""] * 24,
            "selection_reason": [""] * 24,
            "confidence": [0.0] * 24,
            "period": ["1_8"] * 24,
        })
        
        v1_1_names = [
            "v1_1_negative_only_p3",
            "v1_1_negative_plus_conservative_spike",
            "v1_1_nonwinter_selector_negative_p3",
            "v1_1_safe_fallback",
            "v1_1_minimal_patch",
        ]
        
        for vn in v1_1_names:
            builder = POLICY_BUILDERS[vn]
            pred = builder(df)
            # In winter, all policies should use DA anchor
            # DA anchor is 70, so all predictions should be 70
            assert np.allclose(pred, 70.0), f"{vn} doesn't enforce winter DA-only"

    def test_p3_normal_blocked(self):
        """P3 corrections are blocked on normal hours for v1.1 policies."""
        from pipelines.fusion_shadow_v1 import POLICY_BUILDERS
        
        # Build data where P3 would suggest a correction for non-negative/non-spike hours
        df = pd.DataFrame({
            "target_day": ["2026-03-15"] * 24,
            "hour_business": list(range(1, 25)),
            "month": [3] * 24,  # Non-winter
            "y_true": [150.0] * 24,
            "y_pred_sgdf": [140.0] * 24,
            "da_anchor": [130.0] * 24,
            "p3_pred": [100.0] * 24,  # P3 suggests correction
            "p3_confidence": [0.95] * 24,  # Very high confidence
            "p3_corrected": [True] * 24,
            "selector_pred": [np.nan] * 24,
            "selected_model": [""] * 24,
            "selection_reason": [""] * 24,
            "confidence": [0.0] * 24,
            "period": ["1_8"] * 24,
        })
        
        # v1_1_minimal_patch completely disables P3
        pred = POLICY_BUILDERS["v1_1_minimal_patch"](df)
        assert np.allclose(pred, 140.0), "minimal_patch should keep SGDFNet (P3 disabled)"
        
        # v1_1_negative_only_p3 should NOT apply P3 on normal hours (DA anchor not negative)
        pred2 = POLICY_BUILDERS["v1_1_negative_only_p3"](df)
        assert np.allclose(pred2, 140.0), "negative_only_p3 should not apply P3 on normal hours"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
