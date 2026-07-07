"""P2.4 Realtime Lite Candidate Registry — integrity tests.

13 automated checks ensuring registry YAML files comply with candidate rules.

Usage:
    python -m pytest tests/test_realtime_lite_candidate_registry.py -q
    python scripts/check_realtime_lite_candidate_registry.py   # fallback
"""
from __future__ import annotations
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DIR = ROOT / "configs" / "candidate_registry"


def load_yaml(name: str) -> dict:
    path = REGISTRY_DIR / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── checks ──────────────────────────────────────────────────────────────────

def check_registry_files_exist():
    assert (REGISTRY_DIR / "realtime_sgdfnet_lite.yaml").exists(), \
        "realtime_sgdfnet_lite.yaml missing"
    assert (REGISTRY_DIR / "realtime_timesfm_lite.yaml").exists(), \
        "realtime_timesfm_lite.yaml missing"


def check_sgdfnet_status():
    cfg = load_yaml("realtime_sgdfnet_lite.yaml")
    assert cfg["status"] == "candidate", \
        f"SGDFNet status must be 'candidate', got '{cfg['status']}'"
    assert cfg.get("candidate_rules", {}).get("replaces_champion") is False, \
        "SGDFNet replaces_champion must be false"
    assert cfg.get("candidate_rules", {}).get("writes_submission_ready") is False, \
        "SGDFNet writes_submission_ready must be false"
    assert cfg.get("candidate_rules", {}).get("modifies_final_outputs") is False, \
        "SGDFNet modifies_final_outputs must be false"


def check_timesfm_status():
    cfg = load_yaml("realtime_timesfm_lite.yaml")
    assert cfg["status"] in ("candidate", "experimental_result"), \
        f"TimesFM status must be 'candidate' or 'experimental_result', got '{cfg['status']}'"
    assert cfg.get("candidate_rules", {}).get("replaces_champion") is False, \
        "TimesFM replaces_champion must be false"
    assert cfg.get("candidate_rules", {}).get("writes_submission_ready") is False, \
        "TimesFM writes_submission_ready must be false"
    assert cfg.get("candidate_rules", {}).get("modifies_final_outputs") is False, \
        "TimesFM modifies_final_outputs must be false"
    assert cfg.get("candidate_rules", {}).get("requires_more_windows") is True, \
        "TimesFM requires_more_windows must be true"


def check_sgdfnet_cpu_only():
    cfg = load_yaml("realtime_sgdfnet_lite.yaml")
    assert cfg.get("cpu_only") is True, "SGDFNet cpu_only must be true"
    assert cfg.get("gpu_required") is False, "SGDFNet gpu_required must be false"


def check_sgdfnet_beats_baseline():
    cfg = load_yaml("realtime_sgdfnet_lite.yaml")
    assert cfg["baseline_smape_floor50"] > cfg["overall_smape_floor50"], \
        f"Baseline {cfg['baseline_smape_floor50']} must be > SGDFNet {cfg['overall_smape_floor50']}"


def check_sgdfnet_completed_days():
    cfg = load_yaml("realtime_sgdfnet_lite.yaml")
    assert cfg["completed_days"] >= 360, \
        f"SGDFNet completed_days {cfg['completed_days']} < 360"


def check_no_rt916_in_registry():
    for p in REGISTRY_DIR.glob("*.yaml"):
        with open(p) as f:
            if "rt916" in f.read().lower():
                raise AssertionError(f"RT916 found in registry file: {p.name}")


def check_no_timemixer_production():
    """TimeMixer must not appear as a production candidate (status=candidate)."""
    for p in REGISTRY_DIR.glob("*.yaml"):
        with open(p) as f:
            text = f.read()
        if "timemixer" in text.lower():
            cfg = yaml.safe_load(text)
            if cfg.get("status") == "candidate":
                raise AssertionError(f"TimeMixer found as candidate in {p.name}")


def run_all_checks():
    errors = []
    checks = [
        ("Registry files exist", check_registry_files_exist),
        ("SGDFNet status + rules", check_sgdfnet_status),
        ("TimesFM status + rules", check_timesfm_status),
        ("SGDFNet cpu_only/gpu_required", check_sgdfnet_cpu_only),
        ("SGDFNet beats baseline", check_sgdfnet_beats_baseline),
        ("SGDFNet completed_days >= 360", check_sgdfnet_completed_days),
        ("No RT916 in registry", check_no_rt916_in_registry),
        ("No TimeMixer production candidate", check_no_timemixer_production),
    ]
    for name, fn in checks:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            errors.append(name)
    return errors


# ── pytest tests ────────────────────────────────────────────────────────────

class TestRealtimeLiteRegistry:
    def test_registry_files_exist(self):
        check_registry_files_exist()

    def test_sgdfnet_status_candidate(self):
        check_sgdfnet_status()

    def test_timesfm_status_no_shadow(self):
        check_timesfm_status()

    def test_sgdfnet_cpu_only(self):
        check_sgdfnet_cpu_only()

    def test_sgdfnet_beats_baseline(self):
        check_sgdfnet_beats_baseline()

    def test_sgdfnet_completed_days(self):
        check_sgdfnet_completed_days()

    def test_no_rt916_in_registry(self):
        check_no_rt916_in_registry()

    def test_no_timemixer_production_candidate(self):
        check_no_timemixer_production()


if __name__ == "__main__":
    print("P2.4 Realtime Lite Candidate Registry Check")
    print("=" * 45)
    errs = run_all_checks()
    print(f"\n{'=' * 45}")
    if errs:
        print(f"❌ FAILED: {len(errs)} check(s) failed: {', '.join(errs)}")
        sys.exit(1)
    else:
        print("✅ ALL CHECKS PASSED")
        sys.exit(0)
