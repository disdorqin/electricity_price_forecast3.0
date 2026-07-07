from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "candidate_registry" / "system_shadow_coexistence.yaml"


def load_cfg():
    with open(REGISTRY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_coexistence_registry_exists():
    assert REGISTRY.exists()


def test_coexistence_is_safe_shadow_only():
    cfg = load_cfg()
    assert cfg["status"] == "coexistence_safe"
    assert cfg["promotion_level"] == "shadow_monitoring_only"
    assert cfg["recommendation"] == "COEXISTENCE_SAFE"
    assert cfg["result"] == "PASS"
    assert cfg["candidate_rules"]["shadow_only"] is True


def test_no_contamination_recorded():
    cfg = load_cfg()
    audit = cfg["contamination_audit"]
    assert audit["submission_ready_written_days"] == 0
    assert audit["final_dir_written_days"] == 0
    assert audit["exit_code_affected"] is False
    assert audit["delivery_status_changed"] is False
    assert audit["default_off_verified"] is True


def test_overlap_has_no_conflicts():
    cfg = load_cfg()
    overlap = cfg["selector_p3_overlap"]
    assert overlap["conflict_hours"] == 0
    assert overlap["overlap_hours"] == 0
    assert overlap["selector_sgdf_pct"] < 5.0


def test_production_promotion_blocked():
    cfg = load_cfg()
    policy = cfg["monitoring_policy"]
    assert policy["default_enabled"] is False
    assert policy["shadow_only"] is True
    assert policy["production_replacement_allowed"] is False
    assert policy["champion_allowed"] is False


def test_known_limits_are_recorded():
    cfg = load_cfg()
    limits = "\n".join(cfg.get("known_limits", []))
    assert "not pushed" in limits
    assert "replay_from_ledger" in limits
    assert "Overlap hours were zero" in limits
    assert "P3 degraded" in limits
