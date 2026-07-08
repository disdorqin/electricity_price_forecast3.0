"""
Fusion v1.1 Registry Tests

Verifies the candidate registry YAML matches required contract.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = PROJECT_ROOT / "configs" / "candidate_registry" / "fusion_shadow_v1_1.yaml"


def _load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return yaml.safe_load(f)


class TestRegistryExists:
    def test_registry_file_exists(self):
        assert REGISTRY_PATH.exists(), f"Registry not found: {REGISTRY_PATH}"


class TestRegistryContract:
    """Verify all required registry fields."""

    def test_candidate_id(self):
        r = _load_registry()
        assert r.get("candidate_id") == "fusion_shadow_v1_1"

    def test_status_shadow_monitoring_ready(self):
        r = _load_registry()
        assert r.get("status") == "shadow_monitoring_ready", \
            f"Expected shadow_monitoring_ready, got {r.get('status')}"

    def test_promotion_level_shadow_monitoring_only(self):
        r = _load_registry()
        assert r.get("promotion_level") == "shadow_monitoring_only", \
            f"Expected shadow_monitoring_only, got {r.get('promotion_level')}"

    def test_recommendation(self):
        r = _load_registry()
        assert r.get("recommendation") == "SHADOW_MONITORING_READY"

    def test_production_replacement_not_allowed(self):
        r = _load_registry()
        assert r.get("production_replacement_allowed") is False

    def test_champion_not_allowed(self):
        r = _load_registry()
        assert r.get("champion_allowed") is False

    def test_writes_submission_ready_false(self):
        r = _load_registry()
        assert r.get("writes_submission_ready") is False

    def test_modifies_final_outputs_false(self):
        r = _load_registry()
        assert r.get("modifies_final_outputs") is False

    def test_default_enabled_false(self):
        r = _load_registry()
        assert r.get("default_enabled") is False

    def test_improvement_source(self):
        r = _load_registry()
        assert r.get("improvement_source") == "winter_da_anchor_policy"

    def test_p3_coverage_marked_low(self):
        r = _load_registry()
        assert r.get("p3_overlay_effective_coverage") == "low"

    def test_selector_not_main_source(self):
        r = _load_registry()
        assert r.get("selector_not_main_improvement_source") is True

    def test_oracle_analysis_only(self):
        r = _load_registry()
        assert r.get("oracle_analysis_only") is True

    def test_metrics_present(self):
        r = _load_registry()
        m = r.get("metrics", {})
        v = m.get("validation", {})
        assert v.get("official_baseline_smape") == 25.84
        assert v.get("conservative_fusion_v1_smape") == 25.64
        assert v.get("fusion_delta_vs_official") == -0.20
        assert v.get("da_anchor_smape") == 25.59
        assert v.get("da_anchor_delta_vs_official") == -0.25

    def test_source_branch(self):
        r = _load_registry()
        assert "agent/fusion-chain-v1.1-targeted-policy" in r.get("source", {}).get("branch", "")

    def test_tests_passed(self):
        r = _load_registry()
        assert r.get("metrics", {}).get("tests_passed") >= 27


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
