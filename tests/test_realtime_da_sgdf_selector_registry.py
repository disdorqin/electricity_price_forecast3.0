from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "candidate_registry" / "realtime_da_sgdf_selector.yaml"


def load_cfg():
    with open(REGISTRY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def non_winter_metrics(cfg: dict) -> dict:
    return cfg.get("non_winter_metrics", cfg)


def test_selector_registry_exists():
    assert REGISTRY.exists()


def test_selector_is_registry_only_or_seasonal_candidate():
    cfg = load_cfg()
    assert cfg["candidate_id"] == "realtime_da_sgdf_selector"
    assert cfg["task"] == "realtime"
    assert cfg["status"] in ("candidate", "seasonal_candidate")
    assert cfg["promotion_level"] == "registry_only"
    assert cfg["candidate_rules"]["champion_allowed"] is False
    assert cfg["candidate_rules"].get("production_replacement_allowed", False) is False


def test_selector_uses_da_as_primary_baseline():
    cfg = load_cfg()
    assert cfg["baseline_name"] == "DA_anchor"
    assert cfg["selector_policy"]["default_model"] == "DA_anchor"
    assert cfg["selector_policy"]["fallback_model"] == "DA_anchor"
    assert cfg["selector_policy"]["auxiliary_model"] == "SGDFNet"


def test_selector_metrics_are_candidate_level_only():
    cfg = load_cfg()
    metrics = non_winter_metrics(cfg)
    assert metrics["baseline_smape_floor50"] == 19.30
    assert metrics["selector_smape_floor50"] == 19.23
    assert metrics["delta_vs_da_anchor"] < 0
    assert abs(metrics["delta_vs_da_anchor"]) <= 0.10


def test_selector_validation_blocks_ml_gate_promotion():
    cfg = load_cfg()
    assert cfg["validation"]["lomo_ml_beats_da_months"] == 0
    assert cfg["validation"]["time_split_logistic"] >= cfg["validation"]["time_split_da"]
    assert cfg["validation"]["ml_gate_decision"] == "DROP_FOR_PRODUCTION_CRITICAL_PATH"


def test_selector_is_safe_for_3_0_registry_only():
    cfg = load_cfg()
    rules = cfg["candidate_rules"]
    assert rules["writes_submission_ready"] is False
    assert rules["replaces_champion"] is False
    assert rules["modifies_final_outputs"] is False
    assert rules.get("requires_shadow_adapter", True) is True
    assert rules["default_enabled"] is False


def test_winter_policy_blocks_promotion():
    cfg = load_cfg()
    if "season_policy" in cfg:
        assert cfg["season_policy"]["winter_runtime_policy"] == "DA_ONLY_DEFAULT"
        assert cfg["season_policy"]["winter_selector_promotion_allowed"] is False
        assert cfg["candidate_rules"]["winter_promotion_blocked"] is True
