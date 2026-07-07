#!/usr/bin/env python3
"""
P1.1 Dayahead Shadow Registry Fallback Check Script.

Run when pytest is not available: python scripts/check_dayahead_shadow_registry.py

Verifies all 12 contract checks for the 4 shadow registry YAML files.
Returns exit code 0 if all pass, 1 if any fail.
"""

import os
import sys
import yaml

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
REGISTRY_DIR = os.path.join(REPO_ROOT, "configs", "shadow_registry")

REGISTRY_FILES = [
    "dayahead_cfg05.yaml",
    "dayahead_cfg05_180d.yaml",
    "dayahead_xgboost_rich.yaml",
    "dayahead_ensemble_rich.yaml",
]

CHECKS = [
    ("status is shadow", lambda c: c.get("status") == "shadow"),
    ("promotion_level is shadow_only", lambda c: c.get("promotion_level") == "shadow_only"),
    ("writes_submission_ready is false", lambda c: c.get("shadow_rules", {}).get("writes_submission_ready") is False),
    ("replaces_champion is false", lambda c: c.get("shadow_rules", {}).get("replaces_champion") is False),
    ("modifies_final_outputs is false", lambda c: c.get("shadow_rules", {}).get("modifies_final_outputs") is False),
    ("cpu_only is true", lambda c: c.get("cpu_only") is True),
    ("gpu_disabled is true", lambda c: c.get("gpu_disabled") is True),
    ("leakage_check is PASS", lambda c: c.get("leakage_check") == "PASS"),
    ("nan_check is PASS", lambda c: c.get("nan_check") == "PASS"),
    ("hour_completeness_check is PASS", lambda c: c.get("hour_completeness_check") == "PASS"),
    ("beats baseline", lambda c: c.get("baseline_smape_floor50", 0) > c.get("overall_smape_floor50", float("inf"))),
]


def run_checks():
    all_pass = True
    n_total = 0
    n_pass = 0
    n_fail = 0

    for fn in REGISTRY_FILES:
        path = os.path.join(REGISTRY_DIR, fn)
        if not os.path.isfile(path):
            print(f"❌ {fn}: FILE NOT FOUND at {path}")
            all_pass = False
            n_fail += 1
            continue

        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        print(f"\n{'='*60}")
        print(f"  {fn}")
        print(f"{'='*60}")

        for check_name, check_fn in CHECKS:
            n_total += 1
            try:
                result = check_fn(cfg)
                if result:
                    print(f"  ✅ {check_name}")
                    n_pass += 1
                else:
                    print(f"  ❌ {check_name}")
                    n_fail += 1
                    all_pass = False
            except Exception as e:
                print(f"  ❌ {check_name}: ERROR={e}")
                n_fail += 1
                all_pass = False

        # Also verify file existence itself
        n_total += 1
        print(f"  ✅ registry file exists (by iteration)")

    print(f"\n{'='*60}")
    print(f"  Summary: {n_pass}/{n_total} passed, {n_fail} failed")
    print(f"{'='*60}")

    return all_pass


if __name__ == "__main__":
    success = run_checks()
    sys.exit(0 if success else 1)
