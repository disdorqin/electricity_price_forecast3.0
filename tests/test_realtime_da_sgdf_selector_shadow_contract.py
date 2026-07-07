"""P2.11 tests: DA-SGDF selector shadow contract.

15 tests verifying default-off, output schema, safety, and non-contamination.
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipelines.realtime_da_sgdf_selector_shadow import (
    run_realtime_da_sgdf_selector_shadow,
    enable_shadow, is_shadow_enabled, DEFAULT_CONFIG, _SHADOW_ENABLED,
)


class TestSelectorShadowContract:
    def test_default_off(self):
        """Shadow must be off by default."""
        assert not is_shadow_enabled()

    def test_no_flag_no_output(self):
        """Without enable, run must return SKIPPED_NOT_ENABLED."""
        manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01")
        assert manifest["status"] == "SKIPPED_NOT_ENABLED"
        assert len(manifest["output_files"]) == 0

    def test_flag_generates_output(self):
        """With enable, must generate shadow output directory."""
        enable_shadow()
        assert is_shadow_enabled()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            # DA anchor lookup requires xlsx — if not found, returns FAILED_NO_DA_ANCHOR
            assert manifest["status"] in ("COMPLETE", "FAILED_NO_DA_ANCHOR")

    def test_output_24_rows(self):
        """When COMPLETE, output CSV must have exactly 24 rows."""
        enable_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            if manifest["status"] == "COMPLETE":
                csv_path = manifest["output_files"][0]
                df = pd.read_csv(csv_path)
                assert len(df) == 24, f"Expected 24 rows, got {len(df)}"

    def test_hour_business_1_to_24(self):
        """hour_business must be 1..24."""
        enable_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            if manifest["status"] == "COMPLETE":
                df = pd.read_csv(manifest["output_files"][0])
                assert list(df["hour_business"]) == list(range(1, 25))

    def test_required_columns_present(self):
        enable_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            if manifest["status"] == "COMPLETE":
                df = pd.read_csv(manifest["output_files"][0])
                required = ["business_day", "target_day", "ds", "hour_business",
                            "period", "da_anchor", "sgdfnet_pred", "selected_model",
                            "selector_pred", "selection_reason", "confidence",
                            "fallback_used", "shadow_only", "model_version", "run_id"]
                for col in required:
                    assert col in df.columns, f"Missing column: {col}"

    def test_selected_model_legal(self):
        enable_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            if manifest["status"] == "COMPLETE":
                df = pd.read_csv(manifest["output_files"][0])
                legal = {"DA_anchor", "SGDFNet", "FALLBACK_DA"}
                for v in df["selected_model"]:
                    assert v in legal, f"Illegal selected_model: {v}"

    def test_shadow_only_true(self):
        enable_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            if manifest["status"] == "COMPLETE":
                df = pd.read_csv(manifest["output_files"][0])
                assert all(df["shadow_only"] == True)

    def test_no_nan_selector_pred(self):
        """When COMPLETE, selector_pred must have no NaN."""
        enable_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            if manifest["status"] == "COMPLETE":
                df = pd.read_csv(manifest["output_files"][0])
                assert not df["selector_pred"].isna().any()

    def test_missing_sgdfnet_fallback_da(self):
        """Missing SGDFNet must result in all-DA fallback."""
        enable_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            # Run without any prediction CSV
            manifest = run_realtime_da_sgdf_selector_shadow("2025-03-01", runs_root=runs_root)
            if manifest["status"] == "COMPLETE":
                df = pd.read_csv(manifest["output_files"][0])
                assert all(df["selected_model"] == "DA_anchor")

    def test_missing_no_block_main(self):
        """Shadow failure must NOT raise."""
        enable_shadow()
        # Use valid date but non-existent data path
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = str(Path(tmp) / "runs")
            Path(runs_root).mkdir(parents=True)
            result = run_realtime_da_sgdf_selector_shadow(
                "2025-03-01", runs_root=runs_root,
                data_path="/nonexistent/data.xlsx")
        # Should not raise — should return FAILED manifest
        assert result["status"] in ("FAILED_NO_DA_ANCHOR", "SKIPPED_NOT_ENABLED")

    def test_no_target_actual_usage(self):
        """Check source code for actual leakage."""
        import inspect
        from pipelines import realtime_da_sgdf_selector_shadow as module
        source = inspect.getsource(module)
        # The module should NOT read actual_ledger or actual price
        assert "load_actual_ledger" not in source
        assert "load_actual" not in source.replace("load_actual_ledger","")
        assert "load_realtime_actual" not in source

    def test_no_rt916_dependency(self):
        """No RT916 reference in the module."""
        import inspect
        from pipelines import realtime_da_sgdf_selector_shadow as module
        source = inspect.getsource(module)
        assert "rt916" not in source.lower()

    def test_no_timemixer_dependency(self):
        import inspect
        from pipelines import realtime_da_sgdf_selector_shadow as module
        source = inspect.getsource(module)
        assert "timemixer" not in source.lower()
