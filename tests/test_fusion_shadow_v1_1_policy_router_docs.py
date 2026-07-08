"""
Fusion v1.1 Policy Router Docs Tests

Verifies the Seasonal DA Policy Router documentation is accurate,
complete, and follows the required safeguards.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs" / "experiments" / "fusion"

REQUIRED_DOCS = [
    "FUSION_V1_1_ACCEPTANCE_DECISION.md",
    "FUSION_V1_1_SEASONAL_DA_POLICY_ROUTER.md",
    "FUSION_V1_1_2_5_COMPARISON_PLAN.md",
]


def test_all_docs_exist():
    for doc in REQUIRED_DOCS:
        path = DOCS_DIR / doc
        assert path.exists(), f"Missing doc: {path}"


class TestAcceptanceDecisionDoc:
    """Tests for FUSION_V1_1_ACCEPTANCE_DECISION.md."""

    def _read(self) -> str:
        return (DOCS_DIR / "FUSION_V1_1_ACCEPTANCE_DECISION.md").read_text()

    def test_contains_shadow_monitoring_ready(self):
        assert "SHADOW_MONITORING_READY" in self._read()

    def test_contains_winter_da_anchor(self):
        assert "winter" in self._read().lower() and "da" in self._read().lower()

    def test_contains_no_production(self):
        text = self._read().lower()
        assert "not production" in text or "no production" in text or "shadow monitoring" in text

    def test_contains_no_submission_ready(self):
        assert "submission_ready" in self._read()

    def test_contains_next_steps(self):
        """Acceptance doc mentions diagnostic tracking as next step."""
        text = self._read().lower()
        assert "diagnostic" in text or "shadow" in text or "comparison" in text


class TestSeasonalDARouterDoc:
    """Tests for FUSION_V1_1_SEASONAL_DA_POLICY_ROUTER.md."""

    def _read(self) -> str:
        return (DOCS_DIR / "FUSION_V1_1_SEASONAL_DA_POLICY_ROUTER.md").read_text()

    def test_contains_winter_da_anchor(self):
        text = self._read().lower()
        assert "winter" in text and "da anchor" in text

    def test_contains_no_production(self):
        text = self._read().lower()
        assert "not" in text and ("production" in text or "champion" in text)

    def test_contains_no_submission_ready(self):
        assert "submission_ready" in self._read()

    def test_contains_policy_code(self):
        text = self._read()
        assert "if month in" in text or "seasonal_da" in text or "11, 12, 1, 2" in text

    def test_contains_safeguards_section(self):
        assert "BLOCKED" in self._read() or "FORBIDDEN" in self._read()


class TestComparisonPlanDoc:
    """Tests for FUSION_V1_1_2_5_COMPARISON_PLAN.md."""

    def _read(self) -> str:
        return (DOCS_DIR / "FUSION_V1_1_2_5_COMPARISON_PLAN.md").read_text()

    def test_contains_winter_window(self):
        assert "2025-11" in self._read() and "2026-02" in self._read()

    def test_contains_validation_sample(self):
        assert "2026-03" in self._read() and "2026-06" in self._read()

    def test_contains_sanity_day(self):
        assert "2026-07-03" in self._read()

    def test_contains_comparison_variants(self):
        text = self._read()
        assert "2.5" in text and "3.0" in text and "seasonal" in text.lower()

    def test_contains_metrics(self):
        assert "sMAPE" in self._read() or "smape" in self._read()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
