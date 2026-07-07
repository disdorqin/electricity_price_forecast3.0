"""
P1.1 Dayahead Shadow Registry Contract Tests.

Verifies that all 4 shadow registry YAML files satisfy the integration contract.
Run via: python -m pytest tests/test_dayahead_shadow_registry.py -q
"""

import os
import yaml

# Paths
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
REGISTRY_DIR = os.path.join(REPO_ROOT, "configs", "shadow_registry")

# Expected registry files
REGISTRY_FILES = [
    "dayahead_cfg05.yaml",
    "dayahead_cfg05_180d.yaml",
    "dayahead_xgboost_rich.yaml",
    "dayahead_ensemble_rich.yaml",
]

# Canonical baseline for comparison
CANONICAL_BASELINE = 15.0436  # trusted_champion_best_two_average on same window


def load_registry(filename):
    path = os.path.join(REGISTRY_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f), path


# ──────────────────────────────────────
# Test 1: Registry files exist
# ──────────────────────────────────────
def test_registry_files_exist():
    for fn in REGISTRY_FILES:
        path = os.path.join(REGISTRY_DIR, fn)
        assert os.path.isfile(path), f"Missing registry file: {path}"


# ──────────────────────────────────────
# Test 2: Status must be "shadow"
# ──────────────────────────────────────
def test_status_is_shadow():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        assert cfg.get("status") == "shadow", (
            f"{fn}: status={cfg.get('status')!r}, expected 'shadow'"
        )


# ──────────────────────────────────────
# Test 3: promotion_level must be "shadow_only"
# ──────────────────────────────────────
def test_promotion_level_is_shadow_only():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        assert cfg.get("promotion_level") == "shadow_only", (
            f"{fn}: promotion_level={cfg.get('promotion_level')!r}, expected 'shadow_only'"
        )


# ──────────────────────────────────────
# Test 4: writes_submission_ready must be false
# ──────────────────────────────────────
def test_writes_submission_ready_is_false():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        rules = cfg.get("shadow_rules", {})
        assert rules.get("writes_submission_ready") is False, (
            f"{fn}: writes_submission_ready={rules.get('writes_submission_ready')!r}, expected False"
        )


# ──────────────────────────────────────
# Test 5: replaces_champion must be false
# ──────────────────────────────────────
def test_replaces_champion_is_false():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        rules = cfg.get("shadow_rules", {})
        assert rules.get("replaces_champion") is False, (
            f"{fn}: replaces_champion={rules.get('replaces_champion')!r}, expected False"
        )


# ──────────────────────────────────────
# Test 6: modifies_final_outputs must be false
# ──────────────────────────────────────
def test_modifies_final_outputs_is_false():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        rules = cfg.get("shadow_rules", {})
        assert rules.get("modifies_final_outputs") is False, (
            f"{fn}: modifies_final_outputs={rules.get('modifies_final_outputs')!r}, expected False"
        )


# ──────────────────────────────────────
# Test 7: cpu_only must be true
# ──────────────────────────────────────
def test_cpu_only_is_true():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        assert cfg.get("cpu_only") is True, (
            f"{fn}: cpu_only={cfg.get('cpu_only')!r}, expected True"
        )


# ──────────────────────────────────────
# Test 8: gpu_disabled must be true
# ──────────────────────────────────────
def test_gpu_disabled_is_true():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        assert cfg.get("gpu_disabled") is True, (
            f"{fn}: gpu_disabled={cfg.get('gpu_disabled')!r}, expected True"
        )


# ──────────────────────────────────────
# Test 9: leakage_check must be "PASS"
# ──────────────────────────────────────
def test_leakage_check_pass():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        assert cfg.get("leakage_check") == "PASS", (
            f"{fn}: leakage_check={cfg.get('leakage_check')!r}, expected 'PASS'"
        )


# ──────────────────────────────────────
# Test 10: nan_check must be "PASS"
# ──────────────────────────────────────
def test_nan_check_pass():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        assert cfg.get("nan_check") == "PASS", (
            f"{fn}: nan_check={cfg.get('nan_check')!r}, expected 'PASS'"
        )


# ──────────────────────────────────────
# Test 11: hour_completeness_check must be "PASS"
# ──────────────────────────────────────
def test_hour_completeness_check_pass():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        assert cfg.get("hour_completeness_check") == "PASS", (
            f"{fn}: hour_completeness_check={cfg.get('hour_completeness_check')!r}, expected 'PASS'"
        )


# ──────────────────────────────────────
# Test 12: baseline > overall (beats champion)
# ──────────────────────────────────────
def test_beats_baseline():
    for fn in REGISTRY_FILES:
        cfg, path = load_registry(fn)
        overall = cfg.get("overall_smape_floor50")
        baseline = cfg.get("baseline_smape_floor50")
        assert isinstance(overall, (int, float)), f"{fn}: overall_smape_floor50 missing or not a number"
        assert isinstance(baseline, (int, float)), f"{fn}: baseline_smape_floor50 missing or not a number"
        assert baseline > overall, (
            f"{fn}: overall={overall} >= baseline={baseline}, candidate did NOT beat baseline"
        )
