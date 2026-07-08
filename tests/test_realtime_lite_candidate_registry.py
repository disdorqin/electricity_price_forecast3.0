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
    """No realtime candidate registry entry may promote RT916 / TimeMixer to
    production. Negative statements in docs/notes (e.g. 'Never rely on RT916 or
    TimeMixer.') are explicitly allowed; only structured promotion fields are
    asserted, so a blanket substring grep no longer produces a false positive."""
    PRODUCTION_STATUSES = {"production", "promoted", "champion"}
    for p in REGISTRY_DIR.glob("*.yaml"):
        cfg = load_yaml(p.name)

        # status / promotion_level must never be a production promotion
        assert cfg.get("status") not in PRODUCTION_STATUSES, (
            f"{p.name}: status must not be a production promotion "
            f"(got {cfg.get('status')!r})"
        )
        assert cfg.get("promotion_level") != "production", (
            f"{p.name}: promotion_level must not be 'production' "
            f"(got {cfg.get('promotion_level')!r})"
        )

        # explicit production-replacement flags (either location) must be false
        for loc in ("season_policy", "candidate_rules"):
            sub = cfg.get(loc) or {}
            if "production_replacement_allowed" in sub:
                assert sub["production_replacement_allowed"] is False, (
                    f"{p.name}: {loc}.production_replacement_allowed must be false"
                )

        # must never replace the champion
        if "candidate_rules" in cfg:
            assert cfg["candidate_rules"].get("replaces_champion") is not True, (
                f"{p.name}: candidate_rules.replaces_champion must not be true"
            )

        # production feasibility must explicitly declare no RT916 / TimeMixer dep
        # (only enforced for registries that declare a production_feasibility
        # block — candidates that never reference rt916/timemixer are out of scope)
        pf = cfg.get("production_feasibility")
        if pf:
            assert pf.get("no_rt916_dependency") is True, (
                f"{p.name}: production_feasibility.no_rt916_dependency must be true"
            )
            assert pf.get("no_timemixer_dependency") is True, (
                f"{p.name}: production_feasibility.no_timemixer_dependency must be true"
            )

        # online dependency (if declared) must not reference rt916/timemixer
        online = cfg.get("online_dependency")
        if online:
            joined = (
                " ".join(str(x) for x in online).lower()
                if isinstance(online, list)
                else str(online).lower()
            )
            assert "rt916" not in joined, (
                f"{p.name}: online_dependency references rt916"
            )
            assert "timemixer" not in joined, (
                f"{p.name}: online_dependency references timemixer"
            )
