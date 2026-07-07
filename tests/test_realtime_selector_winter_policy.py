from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "candidate_registry" / "realtime_da_sgdf_selector.yaml"


def load_cfg():
    with open(REGISTRY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_winter_no_go_recorded():
    cfg = load_cfg()
    assert cfg["winter_metrics"]["recommendation"] == "WINTER_NO_GO"
    assert cfg["winter_metrics"]["result"] == "PASS"
    assert cfg["winter_metrics"]["days_tested"] == 120
    assert cfg["winter_metrics"]["da_anchor_overall"] < cfg["winter_metrics"]["sgdfnet_overall"]


def test_winter_policy_is_da_only_default():
    cfg = load_cfg()
    policy = cfg["season_policy"]
    assert policy["winter_runtime_policy"] == "DA_ONLY_DEFAULT"
    assert policy["winter_selector_promotion_allowed"] is False
    assert policy["winter_shadow_allowed"] == "diagnostic_only"
    assert set(policy["winter_months"]) == {11, 12, 1, 2}


def test_selector_is_not_production_candidate():
    cfg = load_cfg()
    assert cfg["promotion_level"] == "registry_only"
    assert cfg["candidate_rules"]["default_enabled"] is False
    assert cfg["candidate_rules"]["champion_allowed"] is False
    assert cfg["candidate_rules"]["winter_promotion_blocked"] is True
    assert cfg["candidate_rules"]["production_replacement_allowed"] is False


def test_p3_winter_followup_required():
    cfg = load_cfg()
    risks = "\n".join(cfg.get("known_risks", []))
    followup = "\n".join(cfg.get("required_followup", []))
    assert "P3" in risks
    assert "P3 winter" in followup
