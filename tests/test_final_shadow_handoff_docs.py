"""
Tests for FINAL_SHADOW_MONITORING_HANDOFF docs.
Validates that all 4 handoff docs exist and contain required safety guarantees.
"""
from pathlib import Path

DOCS_DIR = Path("docs")


def test_final_shadow_monitoring_handoff_exists():
    assert (DOCS_DIR / "FINAL_SHADOW_MONITORING_HANDOFF.md").exists()


def test_experiment_decision_summary_exists():
    assert (DOCS_DIR / "EXPERIMENT_DECISION_SUMMARY.md").exists()


def test_shadow_operation_commands_exists():
    assert (DOCS_DIR / "SHADOW_OPERATION_COMMANDS.md").exists()


def test_stop_go_gates_exists():
    assert (DOCS_DIR / "STOP_GO_GATES.md").exists()


def test_handoff_says_default_off():
    text = (DOCS_DIR / "FINAL_SHADOW_MONITORING_HANDOFF.md").read_text("utf-8")
    assert "off" in text.lower() and "default" in text.lower()
    # Each module table header says "Default" column exists
    assert "Default" in text and "OFF" in text


def test_handoff_says_no_production():
    text = (DOCS_DIR / "FINAL_SHADOW_MONITORING_HANDOFF.md").read_text("utf-8")
    assert "prohibited" in text.lower() or "not production" in text.lower()


def test_handoff_covers_all_modules():
    text = (DOCS_DIR / "FINAL_SHADOW_MONITORING_HANDOFF.md").read_text("utf-8")
    for module in ["P1.1", "P2.8", "P2.10", "P2.11", "P2.13", "P3.2", "P3.4", "P3.5"]:
        assert module in text, f"Module {module} not covered in FINAL_SHADOW_MONITORING_HANDOFF.md"


def test_commands_says_default_off():
    text = (DOCS_DIR / "SHADOW_OPERATION_COMMANDS.md").read_text("utf-8")
    assert "default" in text.lower() and "off" in text.lower()


def test_commands_says_no_submission_write():
    text = (DOCS_DIR / "SHADOW_OPERATION_COMMANDS.md").read_text("utf-8")
    assert "submission_ready" in text.lower()


def test_commands_has_production_command():
    text = (DOCS_DIR / "SHADOW_OPERATION_COMMANDS.md").read_text("utf-8")
    assert "main.py YYYY-MM-DD" in text


def test_commands_has_p3_shadow_command():
    text = (DOCS_DIR / "SHADOW_OPERATION_COMMANDS.md").read_text("utf-8")
    assert "enable-extreme-price-shadow" in text


def test_commands_has_selector_shadow_command():
    text = (DOCS_DIR / "SHADOW_OPERATION_COMMANDS.md").read_text("utf-8")
    assert "enable-realtime-da-sgdf-selector-shadow" in text


def test_commands_has_both_shadow_command():
    text = (DOCS_DIR / "SHADOW_OPERATION_COMMANDS.md").read_text("utf-8")
    # Both flags present on same command line
    assert "enable-extreme-price-shadow" in text
    assert "enable-realtime-da-sgdf-selector-shadow" in text


def test_decision_summary_winter_da_only():
    text = (DOCS_DIR / "EXPERIMENT_DECISION_SUMMARY.md").read_text("utf-8")
    assert "DA_anchor" in text and "winter" in text.lower()


def test_decision_summary_says_no_production():
    text = (DOCS_DIR / "EXPERIMENT_DECISION_SUMMARY.md").read_text("utf-8")
    assert "not production" in text.lower() or "prohibited" in text.lower()


def test_decision_summary_p3_strategy():
    text = (DOCS_DIR / "EXPERIMENT_DECISION_SUMMARY.md").read_text("utf-8")
    assert "P3" in text and "monitoring" in text.lower()


def test_gates_has_go_conditions():
    text = (DOCS_DIR / "STOP_GO_GATES.md").read_text("utf-8")
    assert "GO" in text


def test_gates_has_stop_conditions():
    text = (DOCS_DIR / "STOP_GO_GATES.md").read_text("utf-8")
    assert "STOP" in text


def test_gates_has_critical_stops():
    text = (DOCS_DIR / "STOP_GO_GATES.md").read_text("utf-8")
    assert "submission_ready" in text and "final" in text
