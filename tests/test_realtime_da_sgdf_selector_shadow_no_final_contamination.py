"""P2.11 tests: selector shadow must NOT contaminate final or submission_ready."""
from __future__ import annotations
import os, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipelines.realtime_da_sgdf_selector_shadow import (
    run_realtime_da_sgdf_selector_shadow, enable_shadow, DEFAULT_CONFIG,
)


class TestNoFinalContamination:
    def test_output_dir_not_in_final(self):
        """Shadow output dir must not be in final/ or submission_ready."""
        import inspect
        source = inspect.getsource(run_realtime_da_sgdf_selector_shadow)
        # Check function body (excluding docstring) for dangerous patterns
        # We're looking for actual write operations, not comments
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip docstring and comments
            if stripped.startswith('"""') or stripped.startswith("#"):
                continue
            # Check for dangerous write patterns
            if "submission_ready" in stripped:
                pytest.fail(f"Line {i+1}: references submission_ready: {stripped}")
            if "final/" in stripped and "final_outputs" not in stripped and "finalize" not in stripped:
                pytest.fail(f"Line {i+1}: references final/: {stripped}")

    def test_no_final_file_write(self):
        """Shadow outputs must not include final/ paths."""
        import inspect
        source = inspect.getsource(run_realtime_da_sgdf_selector_shadow)
        # The shadow should only write to the shadow output directory
        assert "realtime_da_sgdf_selector_shadow" in source
        # Check it doesn't write to final
        assert "final/" not in source.replace("final_outputs", "").replace("finalize", "")

    def test_no_champion_replaced(self):
        """Must not replace champion."""
        # The module shouldn't import or reference champion-related code
        import pipelines.realtime_da_sgdf_selector_shadow as mod
        # No champion reference in the run function body
        import inspect
        source = inspect.getsource(mod.run_realtime_da_sgdf_selector_shadow)
        assert "champion" not in source.lower()

    def test_no_delivery_status_modification(self):
        """Must not modify delivery_status."""
        import inspect
        source = inspect.getsource(run_realtime_da_sgdf_selector_shadow)
        assert "delivery_status" not in source
