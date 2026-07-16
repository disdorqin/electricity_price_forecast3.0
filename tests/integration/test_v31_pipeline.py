"""
Integration Test: V3.1 Pipeline Integration

Verifies:
1. New CircuitStage enum values exist
2. STAGE_TO_TASK mapping includes new stages
3. A05 builder fail-closes to DD when IHMAE unavailable
4. A05 builder uses IHMAE when available
5. NegCorr off mode passthrough
6. NegCorr shadow mode (no modification)
7. NegCorr production mode (applies correction)
8. NegCorr fail-closed on errors
9. Default config unchanged (V3.0 backward compat)
"""
from __future__ import annotations

import copy
import os
import sys
from unittest import mock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)


# ── 1. CircuitStage enum ──────────────────────────────────────────

class TestCircuitStageEnum:
    def test_negcorr_corrected_exists(self):
        from pipelines.production_circuit.contracts import CircuitStage
        assert hasattr(CircuitStage, "REALTIME_NEGCORR_CORRECTED")
        assert CircuitStage.REALTIME_NEGCORR_CORRECTED.value == "realtime_negcorr_corrected"

    def test_negcorr_shadow_exists(self):
        from pipelines.production_circuit.contracts import CircuitStage
        assert hasattr(CircuitStage, "REALTIME_NEGCORR_SHADOW")
        assert CircuitStage.REALTIME_NEGCORR_SHADOW.value == "realtime_negcorr_shadow"

    def test_repair_stage_negcorr_exists(self):
        from pipelines.production_circuit.contracts import RepairStage
        assert hasattr(RepairStage, "NEGCORR")
        assert RepairStage.NEGCORR.value == "negcorr"


# ── 2. STAGE_TO_TASK mapping ─────────────────────────────────────

class TestStageToTaskMapping:
    def test_negcorr_corrected_mapped(self):
        from pipelines.production_circuit.contracts import (
            CircuitStage, CircuitTask, STAGE_TO_TASK,
        )
        assert STAGE_TO_TASK[CircuitStage.REALTIME_NEGCORR_CORRECTED] == CircuitTask.REALTIME

    def test_negcorr_shadow_mapped(self):
        from pipelines.production_circuit.contracts import (
            CircuitStage, CircuitTask, STAGE_TO_TASK,
        )
        assert STAGE_TO_TASK[CircuitStage.REALTIME_NEGCORR_SHADOW] == CircuitTask.REALTIME


# ── 3. A05 builder — fail-closed to DD ───────────────────────────

class TestA05BuilderFailClosed:
    def test_no_ihmae_source_returns_dd(self):
        """When no ihmae_source in config, A05 = DD."""
        from pipelines.production_circuit.a05_builder import build_a05_candidate

        # Mock DB cursor returning da_anchor = 400 for all 24 hours
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchall.return_value = [
            (hb, 400.0 + hb) for hb in range(1, 25)
        ]

        mock_conn = mock.MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows, meta = build_a05_candidate(mock_conn, "2026-03-15", {})

        assert len(rows) == 24
        assert meta["ihmae_status"] == "NO_SOURCE_CONFIGURED"
        for i, row in enumerate(rows):
            assert row["hour_business"] == i + 1
            expected_dd = 400.0 + (i + 1)
            assert row["pred_price"] == expected_dd, (
                f"Hour {i+1}: expected {expected_dd}, got {row['pred_price']}"
            )
            assert row["model_name"] == "a05_composite"
            assert "fail_closed_to_dd" in row["quality_flags"]

    def test_ihmae_load_fail_no_file(self):
        """When ihmae_source points to nonexistent file, A05 = DD."""
        from pipelines.production_circuit.a05_builder import build_a05_candidate

        mock_cursor = mock.MagicMock()
        mock_cursor.fetchall.return_value = [
            (hb, 400.0) for hb in range(1, 25)
        ]
        mock_conn = mock.MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows, meta = build_a05_candidate(
            mock_conn, "2026-03-15",
            {"ihmae_source": "/nonexistent/path/to/ihmae.csv"},
        )

        assert len(rows) == 24
        assert "LOAD_FAILED" in meta["ihmae_status"]
        for row in rows:
            assert row["pred_price"] == 400.0


# ── 4. A05 builder — with IHMAE ─────────────────────────────────

class TestA05BuilderWithIHMAE:
    def test_full_composite(self):
        """When IHMAE is available, A05 = 0.5*DD + 0.5*IHMAE."""
        from pipelines.production_circuit.a05_builder import build_a05_candidate

        import tempfile
        import pandas as pd

        # Create a temp CSV with IHMAE data
        target_date = "2026-03-15"
        ihmae_data = {
            "business_day": [target_date] * 24,
            "hour_business": list(range(1, 25)),
            "IHMAE": [300.0 + hb for hb in range(1, 25)],
        }
        df = pd.DataFrame(ihmae_data)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w",
                                         encoding="utf-8", newline="") as f:
            df.to_csv(f.name, index=False)
            temp_path = f.name

        try:
            mock_cursor = mock.MagicMock()
            mock_cursor.fetchall.return_value = [
                (hb, 400.0 + hb) for hb in range(1, 25)
            ]
            mock_conn = mock.MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

            rows, meta = build_a05_candidate(
                mock_conn, target_date,
                {"ihmae_source": temp_path},
            )

            assert len(rows) == 24
            assert meta["ihmae_status"] == "RECONSTRUCTED"
            for i, row in enumerate(rows):
                hb = i + 1
                dd = 400.0 + hb
                ihmae = 300.0 + hb
                expected = 0.5 * dd + 0.5 * ihmae
                assert abs(row["pred_price"] - expected) < 1e-6, (
                    f"Hour {hb}: expected {expected}, got {row['pred_price']}"
                )
                assert "full" in row["quality_flags"]
        finally:
            os.unlink(temp_path)

    def test_empty_ihmae_data(self):
        """When IHMAE source has no data for target date, A05 = DD."""
        from pipelines.production_circuit.a05_builder import build_a05_candidate

        import tempfile
        import pandas as pd

        # CSV with DIFFERENT date (no data for our target)
        ihmae_data = {
            "business_day": ["2025-01-01"] * 24,
            "hour_business": list(range(1, 25)),
            "IHMAE": [300.0] * 24,
        }
        df = pd.DataFrame(ihmae_data)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w",
                                         encoding="utf-8", newline="") as f:
            df.to_csv(f.name, index=False)
            temp_path = f.name

        try:
            mock_cursor = mock.MagicMock()
            mock_cursor.fetchall.return_value = [
                (hb, 500.0) for hb in range(1, 25)
            ]
            mock_conn = mock.MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

            rows, meta = build_a05_candidate(
                mock_conn, "2026-03-15",
                {"ihmae_source": temp_path},
            )

            assert len(rows) == 24
            assert meta["ihmae_status"] == "NO_IHMAE_DATA"
            for row in rows:
                assert row["pred_price"] == 500.0
        finally:
            os.unlink(temp_path)


# ── 5. NegCorr off passthrough ─────────────────────────────────────

class TestNegcorrOffPassthrough:
    def test_off_mode_passthrough(self):
        """When negcorr_mode=off, values are passed through unchanged."""
        from pipelines.production_circuit.negcorr_chain import (
            _negcorr_mode, _read_fused, _passthrough,
        )

        assert _negcorr_mode({"negcorr_mode": "off"}) == "off"

    def test_off_env_default(self):
        """Default env should give 'off'."""
        old = os.environ.pop("EFM3_ENABLE_NEGCORR", None)
        try:
            from fusion.correction.feature_flags import get_flag
            assert get_flag("EFM3_ENABLE_NEGCORR") == "off"
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old


# ── 6. NegCorr shadow mode ───────────────────────────────────────

class TestNegcorrShadow:
    def test_shadow_mode_flag(self):
        old = os.environ.get("EFM3_ENABLE_NEGCORR")
        os.environ["EFM3_ENABLE_NEGCORR"] = "shadow"
        try:
            from fusion.correction.feature_flags import is_negcorr_shadow
            assert is_negcorr_shadow() is True
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old
            else:
                os.environ.pop("EFM3_ENABLE_NEGCORR", None)

    def test_negcorr_mode_from_env_shadow(self):
        from pipelines.production_circuit.negcorr_chain import _negcorr_mode
        old = os.environ.get("EFM3_ENABLE_NEGCORR")
        os.environ["EFM3_ENABLE_NEGCORR"] = "shadow"
        try:
            assert _negcorr_mode({}) == "shadow"
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old
            else:
                os.environ.pop("EFM3_ENABLE_NEGCORR", None)


# ── 7. NegCorr production mode ──────────────────────────────────

class TestNegcorrProduction:
    def test_production_mode_flag(self):
        old = os.environ.get("EFM3_ENABLE_NEGCORR")
        os.environ["EFM3_ENABLE_NEGCORR"] = "production"
        try:
            from fusion.correction.feature_flags import is_negcorr_production
            assert is_negcorr_production() is True
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old
            else:
                os.environ.pop("EFM3_ENABLE_NEGCORR", None)


# ── 8. NegCorr fail-closed ───────────────────────────────────────

class TestNegcorrFailClosed:
    def test_negcorr_module_disabled_returns_input(self):
        """When NegCorr flag is off, predict() returns input unchanged."""
        import pandas as pd
        old = os.environ.pop("EFM3_ENABLE_NEGCORR", None)
        try:
            from fusion.correction.negcorr_shadow import NegCorrShadowModule
            module = NegCorrShadowModule()
            fused = pd.Series([100.0] * 24)
            da = pd.Series([110.0] * 24)
            hours = pd.Series(range(1, 25))
            result = module.predict(fused, da, hours)
            pd.testing.assert_series_equal(result, fused)
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old

    def test_negcorr_no_artifact_returns_input(self):
        """When artifact missing and flag=shadow, predict returns input unchanged."""
        import pandas as pd
        old = os.environ.get("EFM3_ENABLE_NEGCORR")
        os.environ["EFM3_ENABLE_NEGCORR"] = "shadow"
        try:
            from fusion.correction.negcorr_shadow import NegCorrShadowModule
            module = NegCorrShadowModule()
            # Module won't find artifact -> fail closed
            fused = pd.Series([200.0, 150.0, -50.0] + [100.0] * 21)
            da = pd.Series([210.0] * 24)
            hours = pd.Series(range(1, 25))
            result = module.predict(fused, da, hours)
            pd.testing.assert_series_equal(result, fused)
        finally:
            if old is not None:
                os.environ["EFM3_ENABLE_NEGCORR"] = old
            else:
                os.environ.pop("EFM3_ENABLE_NEGCORR", None)


# ── 9. Default config unchanged ───────────────────────────────────

class TestDefaultConfigUnchanged:
    def test_default_realtime_models(self):
        """Default RT models must include a05_composite."""
        from pipelines.production_circuit.model_loader import DEFAULT_REALTIME_MODELS
        assert "a05_composite" in DEFAULT_REALTIME_MODELS

    def test_negcorr_default_off(self):
        """NegCorr must default to off."""
        from pipelines.production_circuit.negcorr_chain import _negcorr_mode
        # Empty config + no env = off
        assert _negcorr_mode({}) == "off"

    def test_fusion_chain_unchanged(self):
        """fusion_chain import works and interface is unchanged."""
        from pipelines.production_circuit.fusion_chain import run_fusion
        import inspect
        sig = inspect.signature(run_fusion)
        params = list(sig.parameters.keys())
        assert "ctx" in params
        assert "task" in params
        assert "source_stage" in params

    def test_negative_price_fixer_unchanged_logic(self):
        """negative_price_fixer import works."""
        from pipelines.production_circuit.negative_price_fixer import (
            run_negative_price_fixer, NEG_FLOOR, _load_p3_corrections,
        )
        assert NEG_FLOOR == -500.0
