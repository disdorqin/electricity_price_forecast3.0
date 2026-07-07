from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "candidate_registry" / "extreme_price_shadow_winter.yaml"


def load_cfg():
    with open(REGISTRY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_winter_monitoring_registry_exists():
    assert REGISTRY.exists()


def test_winter_monitoring_status_and_result():
    cfg = load_cfg()
    assert cfg["status"] == "shadow_monitoring_ready"
    assert cfg["promotion_level"] == "shadow_monitoring_only"
    assert cfg["recommendation"] == "WINTER_SHADOW_MONITORING_READY"
    assert cfg["result"] == "PASS"
    assert cfg["days_tested"] == 120


def test_negative_benefit_is_recorded():
    cfg = load_cfg()
    neg = cfg["scene_overall"]["negative"]
    assert neg["delta"] < -10.0
    assert neg["corrected"] < neg["original"]


def test_normal_tradeoff_blocks_production():
    cfg = load_cfg()
    normal = cfg["scene_overall"]["normal"]
    assert normal["delta"] > 0
    assert cfg["candidate_rules"]["production_replacement_allowed"] is False
    assert cfg["candidate_rules"]["champion_allowed"] is False


def test_no_final_or_submission_writes_allowed():
    cfg = load_cfg()
    rules = cfg["candidate_rules"]
    assert rules["writes_submission_ready"] is False
    assert rules["replaces_champion"] is False
    assert rules["modifies_final_outputs"] is False
    assert rules["default_enabled"] is False
    assert rules["shadow_only"] is True


def test_known_limits_require_followup():
    cfg = load_cfg()
    limits = "\n".join(cfg.get("known_limits", []))
    followup = "\n".join(cfg.get("required_followup", []))
    assert "replay_from_ledger" in limits
    assert "coexistence" in limits
    assert "source_commit" in limits
    assert "selector + P3 coexistence" in followup
