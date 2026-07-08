from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "docs" / "experiments" / "fusion"

DOCS = [
    "FUSION_V1_1_ACCEPTANCE_DECISION.md",
    "FUSION_V1_1_SEASONAL_DA_POLICY_ROUTER.md",
    "FUSION_V1_1_2_5_COMPARISON_PLAN.md",
    "FUSION_V1_1_FINALIZATION_REPORT.md",
]


def read(name):
    return (DOC_DIR / name).read_text(encoding="utf-8")


def test_docs_exist():
    for name in DOCS:
        assert (DOC_DIR / name).exists(), name


def test_acceptance_decision_is_honest():
    text = read("FUSION_V1_1_ACCEPTANCE_DECISION.md").lower()
    assert "0.20" in text
    assert "borderline" in text
    assert "not be overstated" in text
    assert "not production" in text or "production replacement" in text


def test_policy_router_definition():
    text = read("FUSION_V1_1_SEASONAL_DA_POLICY_ROUTER.md")
    assert "month in (11, 12, 1, 2)" in text
    assert "da_anchor" in text
    assert "official_baseline" in text


def test_no_submission_or_champion_allowed():
    combined = "\n".join(read(name).lower() for name in DOCS)
    assert "submission_ready" in combined
    assert "champion" in combined
    assert "final output" in combined or "final/" in combined


def test_2_5_comparison_limit_recorded():
    text = read("FUSION_V1_1_2_5_COMPARISON_PLAN.md")
    assert "2_5_status: unavailable_or_cached_only" in text


def test_final_report_names_true_source():
    text = read("FUSION_V1_1_FINALIZATION_REPORT.md").lower()
    assert "winter da anchor" in text
    assert "not a complex" in text or "seasonal da policy router" in text
    assert "oracle" in text
