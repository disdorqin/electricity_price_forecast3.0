from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DIR = ROOT / "configs" / "candidate_registry"


def load_yaml(name: str) -> dict:
    with open(REGISTRY_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_registry_files_exist():
    assert (REGISTRY_DIR / "realtime_sgdfnet_lite.yaml").exists()
    assert (REGISTRY_DIR / "realtime_timesfm_lite.yaml").exists()


def test_sgdfnet_registry_is_safe():
    cfg = load_yaml("realtime_sgdfnet_lite.yaml")
    assert cfg["status"] in ("candidate", "seasonal_candidate")
    assert cfg["promotion_level"] == "registry_only"
    rules = cfg["candidate_rules"]
    assert rules["writes_submission_ready"] is False
    assert rules["replaces_champion"] is False
    assert rules["modifies_final_outputs"] is False


def test_sgdfnet_canonical_correction():
    cfg = load_yaml("realtime_sgdfnet_lite.yaml")
    assert cfg["canonical_hour_mapping"] is True
    assert cfg["beats_baseline"] is False
    assert cfg["baseline_name"] == "DA_anchor"
    assert cfg["baseline_smape_floor50"] < cfg["overall_smape_floor50"]
    assert cfg["delta_vs_baseline"] > 0
    assert cfg["monthly_wins_vs_da"] == 3
    assert cfg["monthly_losses_vs_da"] == 7
    assert cfg["candidate_rules"]["requires_da_aware_gate"] is True


def test_sgdfnet_runtime_and_coverage():
    cfg = load_yaml("realtime_sgdfnet_lite.yaml")
    assert cfg["cpu_only"] is True
    assert cfg["gpu_required"] is False
    assert cfg["completed_days"] >= 360
    assert cfg["runtime_sec_per_day"] <= 60


def test_timesfm_registry_is_experimental_only():
    cfg = load_yaml("realtime_timesfm_lite.yaml")
    assert cfg["status"] in ("experimental_result", "candidate", "seasonal_candidate")
    assert cfg["candidate_rules"]["writes_submission_ready"] is False
    assert cfg["candidate_rules"]["replaces_champion"] is False
    assert cfg["candidate_rules"]["modifies_final_outputs"] is False
    assert cfg["candidate_rules"]["requires_more_windows"] is True


def test_no_rt916_or_timemixer_production_candidate():
    for p in REGISTRY_DIR.glob("*.yaml"):
        text = p.read_text(encoding="utf-8").lower()
        if "rt916" in text:
            raise AssertionError(f"rt916 must not be in realtime candidate registry: {p.name}")
        if "timemixer" in text:
            cfg = yaml.safe_load(text)
            assert cfg.get("status") != "candidate"
