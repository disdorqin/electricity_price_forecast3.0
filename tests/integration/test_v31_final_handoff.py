"""
Integration Test: V3.1-FINAL Handoff Registry Verification

Verifies:
1. Model registry files exist and are well-formed
2. A05 is PRIMARY and UNCHANGED
3. All research candidates have default_enabled: false
4. Feature flags default to off
5. NegCorr shadow module fails closed
6. Deprecated models are not registered for integration
7. Artifact files exist
8. DO_NOT_INTEGRATE list is respected
"""
from __future__ import annotations

import os
import sys
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ── 1. Registry files exist ──────────────────────────────────────

class TestRegistryFilesExist:
    def test_production_models_yaml(self):
        p = REPO_ROOT / "configs" / "model_registry" / "production_models.yaml"
        assert p.exists(), f"Missing: {p}"

    def test_model_registry_full_yaml(self):
        p = REPO_ROOT / "configs" / "model_registry" / "MODEL_REGISTRY.yaml"
        assert p.exists(), f"Missing: {p}"

    def test_integration_roles_yaml(self):
        p = REPO_ROOT / "configs" / "model_registry" / "INTEGRATION_ROLES.yaml"
        assert p.exists(), f"Missing: {p}"

    def test_model_changelog_yaml(self):
        p = REPO_ROOT / "configs" / "model_registry" / "MODEL_CHANGELOG.yaml"
        assert p.exists(), f"Missing: {p}"


# ── 2. A05 is PRIMARY and UNCHANGED ─────────────────────────────

class TestA05Unchanged:
    def _read_registry(self):
        p = REPO_ROOT / "configs" / "model_registry" / "production_models.yaml"
        return p.read_text(encoding="utf-8")

    def test_a05_is_primary(self):
        content = self._read_registry()
        assert "A05:" in content
        a05_section = content.split("A05:")[1].split("\n\n")[0]
        assert "integration_role: PRIMARY" in a05_section

    def test_a05_unchanged(self):
        content = self._read_registry()
        a05_section = content.split("A05:")[1].split("\n\n")[0]
        assert "change_type: UNCHANGED" in a05_section

    def test_a05_default_enabled(self):
        content = self._read_registry()
        a05_section = content.split("A05:")[1].split("\n\n")[0]
        assert "default_enabled: true" in a05_section


# ── 3. Research candidates default_enabled: false ────────────────

class TestResearchCandidatesDisabled:
    def _read_registry(self):
        p = REPO_ROOT / "configs" / "model_registry" / "MODEL_REGISTRY.yaml"
        return p.read_text(encoding="utf-8")

    def test_negcorr_w120_disabled(self):
        content = self._read_registry()
        section = content.split("NegCorr_w120_V5_CANONICAL:")[1].split("\n\n")[0]
        assert "default_enabled: false" in section

    def test_negcorr_w180_disabled(self):
        content = self._read_registry()
        section = content.split("NegCorr_w180_V5_CANONICAL:")[1].split("\n\n")[0]
        assert "default_enabled: false" in section

    def test_negcorr_v51_do_not_enable(self):
        content = self._read_registry()
        section = content.split("NegCorr_w120_V51_ADAPTIVE_PREWARM:")[1].split("\n\n")[0]
        assert "integration_role: DO_NOT_ENABLE" in section

    def test_undercorr_do_not_enable(self):
        content = self._read_registry()
        section = content.split("UnderCorr_reference:")[1].split("\n\n")[0]
        assert "integration_role: DO_NOT_ENABLE" in section
        assert "default_enabled: false" in section


# ── 4. Feature flags default off ─────────────────────────────────

class TestFeatureFlagsDefaultOff:
    def test_negcorr_flag_default_off(self):
        # Ensure flag is NOT set
        old = os.environ.pop("EFM3_ENABLE_NEGCORR", None)
        try:
            from fusion.correction.feature_flags import get_flag
            assert get_flag("EFM3_ENABLE_NEGCORR") == "off"
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old

    def test_negcorr_not_enabled_by_default(self):
        old = os.environ.pop("EFM3_ENABLE_NEGCORR", None)
        try:
            from fusion.correction.feature_flags import is_negcorr_enabled
            assert is_negcorr_enabled() is False
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old


# ── 5. NegCorr shadow fails closed ──────────────────────────────

class TestNegCorrFailClosed:
    def test_disabled_returns_a05(self):
        """When flag is off, predict() returns A05 unchanged."""
        import pandas as pd
        old = os.environ.pop("EFM3_ENABLE_NEGCORR", None)
        try:
            from fusion.correction.negcorr_shadow import NegCorrShadowModule
            module = NegCorrShadowModule()
            a05 = pd.Series([100.0] * 24)
            da = pd.Series([110.0] * 24)
            hours = pd.Series(range(1, 25))
            result = module.predict(a05, da, hours)
            pd.testing.assert_series_equal(result, a05)
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old

    def test_shadow_mode_flag(self):
        old = os.environ.get("EFM3_ENABLE_NEGCORR", None)
        os.environ["EFM3_ENABLE_NEGCORR"] = "shadow"
        try:
            from fusion.correction.feature_flags import is_negcorr_shadow
            assert is_negcorr_shadow() is True
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old
            else:
                os.environ.pop("EFM3_ENABLE_NEGCORR", None)


# ── 6. Deprecated models not in production registry ──────────────

class TestDeprecatedNotRegistered:
    def test_no_pc1_in_production(self):
        p = REPO_ROOT / "configs" / "model_registry" / "production_models.yaml"
        content = p.read_text(encoding="utf-8")
        assert "PC1" not in content

    def test_no_central_expert(self):
        p = REPO_ROOT / "configs" / "model_registry" / "production_models.yaml"
        content = p.read_text(encoding="utf-8")
        assert "Central_expert" not in content

    def test_no_spike_expert(self):
        p = REPO_ROOT / "configs" / "model_registry" / "production_models.yaml"
        content = p.read_text(encoding="utf-8")
        assert "Spike_expert" not in content


# ── 7. Artifact files exist ──────────────────────────────────────

class TestArtifactsExist:
    def test_negcorr_pkl(self):
        p = REPO_ROOT / "artifacts" / "negcorr" / "negcorr_w120_w180.pkl"
        assert p.exists(), f"Missing NegCorr artifact: {p}"
        assert p.stat().st_size > 0

    def test_canonical_panel_parquet(self):
        p = REPO_ROOT / "artifacts" / "canonical_panel" / "FAILMODE_V5_CANONICAL_PANEL.parquet"
        assert p.exists(), f"Missing canonical panel: {p}"
        assert p.stat().st_size > 0


# ── 8. DO_NOT_INTEGRATE list ─────────────────────────────────────

class TestDoNotIntegrate:
    def test_do_not_integrate_file_exists(self):
        p = REPO_ROOT / "docs" / "integration" / "DO_NOT_INTEGRATE.txt"
        assert p.exists()

    def test_lists_infra_blocked(self):
        p = REPO_ROOT / "docs" / "integration" / "DO_NOT_INTEGRATE.txt"
        content = p.read_text(encoding="utf-8")
        assert "INFRA_BLOCKED" in content

    def test_lists_rejected_models(self):
        p = REPO_ROOT / "docs" / "integration" / "DO_NOT_INTEGRATE.txt"
        content = p.read_text(encoding="utf-8")
        for model in ["PC1_curve_correction", "Central_expert", "Spike_expert", "Original_trident"]:
            assert model in content, f"Missing rejected model: {model}"


# ── 9. Integration documentation ─────────────────────────────────

class TestIntegrationDocs:
    def test_handoff_readme(self):
        p = REPO_ROOT / "docs" / "integration" / "handoff" / "00_READ_BY_INTEGRATION_AI.md"
        assert p.exists()

    def test_final_state(self):
        p = REPO_ROOT / "docs" / "integration" / "01_FINAL_STATE.yaml"
        assert p.exists()
        content = p.read_text(encoding="utf-8")
        assert "FINAL_BLOCKED_EVIDENCE_PACKAGED" in content

    def test_technical_report(self):
        p = REPO_ROOT / "docs" / "integration" / "reports" / "EFM3_FINAL_TECHNICAL_REPORT.md"
        assert p.exists()

    def test_file_copy_map(self):
        p = REPO_ROOT / "docs" / "integration" / "handoff" / "FILE_COPY_MAP.csv"
        assert p.exists()

    def test_entrypoints(self):
        p = REPO_ROOT / "docs" / "integration" / "handoff" / "ENTRYPOINTS.yaml"
        assert p.exists()
