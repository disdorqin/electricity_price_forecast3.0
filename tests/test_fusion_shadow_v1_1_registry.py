from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "candidate_registry" / "fusion_shadow_v1_1.yaml"


def load_cfg():
    with open(REGISTRY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_registry_exists():
    assert REGISTRY.exists()


def test_status_shadow_monitoring_only():
    cfg = load_cfg()
    assert cfg["status"] == "shadow_monitoring_ready"
    assert cfg["promotion_level"] == "shadow_monitoring_only"
    assert cfg["recommendation"] == "SHADOW_MONITORING_READY"
    assert cfg["result"] == "PASS"


def test_production_guards():
    cfg = load_cfg()
    assert cfg["production_replacement_allowed"] is False
    assert cfg["champion_allowed"] is False
    assert cfg["writes_submission_ready"] is False
    assert cfg["modifies_final_outputs"] is False
    assert cfg["default_enabled"] is False


def test_improvement_source_is_winter_da_anchor():
    cfg = load_cfg()
    assert cfg["true_policy_name"] == "seasonal_da_policy_router"
    assert cfg["improvement_source"] == "winter_da_anchor_policy"
    assert cfg["selector_not_main_improvement_source"] is True
    assert cfg["p3_overlay_effective_coverage"] == "low"


def test_metrics_are_threshold_level_not_overstated():
    cfg = load_cfg()
    val = cfg["metrics"]["validation"]
    assert val["official_baseline_smape"] == 25.84
    assert val["conservative_fusion_v1_smape"] == 25.64
    assert val["fusion_delta_vs_official"] == -0.20
    assert val["da_anchor_smape"] < val["conservative_fusion_v1_smape"]
    assert val["realtime_selector_smape"] > val["official_baseline_smape"]


def test_oracle_is_analysis_only():
    cfg = load_cfg()
    assert cfg["oracle_analysis_only"] is True


def test_2_5_status_recorded():
    cfg = load_cfg()
    assert cfg["metrics"]["comparison_sample"]["two_five_status"] == "unavailable_or_cached_only"
