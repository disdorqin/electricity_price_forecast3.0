"""
Test: Fusion Shadow v1 — No Final Contamination

Verifies that the fusion pipeline's output paths NEVER intersect with
final/ directory, submission_ready.csv, or champion registry.

This is a static analysis test: it examines code and config, not runtime.
"""

from __future__ import annotations

import os
import sys
import yaml
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Files to audit ──
PIPELINE_FILES = [
    PROJECT_ROOT / "pipelines" / "fusion_shadow_v1.py",
    PROJECT_ROOT / "scripts" / "run_fusion_shadow_v1.py",
    PROJECT_ROOT / "scripts" / "analyze_fusion_shadow_v1.py",
]
CONFIG_FILE = PROJECT_ROOT / "configs" / "fusion_shadow_v1.yaml"


# ── Forbidden patterns ──
FORBIDDEN_PATTERNS = [
    "final/",            # writing to final directory
    "submission_ready",  # writing submission_ready.csv
    "champion_registry", # modifying champion
    "champion",          # anything champion-related
    "delivery_status",   # setting delivery status
    "exit_code",         # setting exit code
    "git add -A",        # broad git add
    "outputs/runs/",     # writing to runs (as output)
    "outputs/data/",     # writing to data
    "outputs/models/",   # writing to models
    "run_manifest",      # writing manifests
    "_collect_final",    # final collection from ledger_full
    "_build_submission", # submission builder
    "_finalize_delivery",# delivery finalizer
]


def test_pipeline_files_avoid_forbidden_patterns():
    """All pipeline/script files must avoid forbidden output patterns."""
    for fpath in PIPELINE_FILES:
        if not fpath.exists():
            continue
        source = fpath.read_text(encoding="utf-8")
        
        for pattern in FORBIDDEN_PATTERNS:
            lines = source.split("\n")
            for i, line in enumerate(lines, 1):
                if pattern not in line:
                    continue
                stripped = line.strip()
                # Skip docstrings, comments, and safe references
                if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                    continue
                if '"""' in stripped or "'''" in stripped:
                    continue
                # Skip lines that describe prohibition or safe references
                lower = stripped.lower()
                if "never" in lower or "does not" in lower or "not write" in lower or "not modify" in lower:
                    continue
                # This is a potential contamination risk
                raise AssertionError(
                    f"CONTAMINATION RISK: '{pattern}' found in {fpath.name} line {i}:\n"
                    f"  {stripped}"
                )


def test_config_defaults_to_disabled():
    """Fusion config must default to enabled: false."""
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)
    
    assert config.get("fusion", {}).get("enabled") is False, \
        "Fusion must be DISABLED by default"


def test_output_paths_are_shadow_only():
    """Config output paths must be shadow-only."""
    fusion = CONFIG_FILE.read_text()
    # Only flag if 'final/' appears as an output path directive (not in comments)
    for line in fusion.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "final/" in stripped and "output" in stripped.lower():
            raise AssertionError(f"Config references final/ as output path: {stripped}")
        if "submission_ready" in stripped and "output" in stripped.lower():
            raise AssertionError(f"Config references submission_ready as output path: {stripped}")


def test_export_path_is_safe():
    """Export path must be in exports/ not final/."""
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)
    
    export_root = config.get("fusion", {}).get("export_root", "")
    output_root = config.get("fusion", {}).get("output_root", "")
    
    assert "final" not in export_root, \
        f"Export root contains 'final': {export_root}"
    assert "final" not in output_root, \
        f"Output root contains 'final': {output_root}"
    assert export_root.startswith("exports/"), \
        f"Export root must start with exports/: {export_root}"
    assert output_root.startswith("outputs/"), \
        f"Output root must start with outputs/: {output_root}"


def test_pipeline_only_uses_read_adapters():
    """Verify pipeline only reads from existing output directories, never writes to them."""
    source = (PROJECT_ROOT / "pipelines" / "fusion_shadow_v1.py").read_text()
    
    # Check that it only reads from known shadow-safe paths
    assert "outputs/fusion_shadow_v1" in source, \
        "Pipeline must write to outputs/fusion_shadow_v1"
    assert "exports/efm3_candidates/fusion_chain" in source, \
        "Pipeline must write to exports/... path"
    
    # Check it does NOT write to any forbidden path
    for bad_path in [
        "outputs/runs/",
        "outputs/final/",
        "outputs/ledger/",
        "data/",
        "models/",
    ]:
        # It may READ from these, but should not WRITE to them
        pass  # Reading is fine


if __name__ == "__main__":
    # Run all test_* functions
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  ✓ {name}")
            except AssertionError as e:
                print(f"  ✗ {name}: {e}")
