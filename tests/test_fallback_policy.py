"""Fallback policy — the dry_run vs formal matrix must behave as specified."""

from common.fallback_policy import (
    evaluate_db_failure,
    evaluate_dataset_failure,
    evaluate_router_failure,
    evaluate_postflight_failure,
    evaluate_export_failure,
    evaluate_shadow_failure,
    map_to_exit_code,
)


def test_db_unavailable_dry_run_falls_back_to_file_store():
    d = evaluate_db_failure("dry_run")
    assert d.status == "PARTIAL"
    assert d.delivery_status == "PARTIAL"
    assert d.exit_code == 0
    assert d.db_enabled is False
    assert d.action == "continue_file_store"


def test_db_unavailable_formal_fails():
    d = evaluate_db_failure("formal")
    assert d.status == "FAIL"
    assert d.delivery_status == "FAILED_NO_DELIVERY"
    assert d.exit_code == 1
    assert map_to_exit_code(d) == 1


def test_dataset_not_ready_formal_fails():
    d = evaluate_dataset_failure("formal", ready_dataset_exists=False)
    assert d.exit_code == 1
    assert d.delivery_status == "FAILED_NO_DELIVERY"


def test_dataset_not_ready_dry_run_degraded_if_ready_exists():
    d = evaluate_dataset_failure("dry_run", ready_dataset_exists=True)
    assert d.status == "DEGRADED"
    assert d.delivery_status == "DEGRADED_DELIVERED"


def test_da_anchor_missing_winter_formal_fails_without_override():
    d = evaluate_router_failure("formal", is_winter=True, da_anchor_missing=True)
    assert d.exit_code == 1
    # With explicit override it degrades instead of failing.
    d2 = evaluate_router_failure(
        "formal", is_winter=True, da_anchor_missing=True, allow_router_fallback=True,
    )
    assert d2.exit_code == 0


def test_da_anchor_missing_nonwinter_falls_back_to_baseline():
    d = evaluate_router_failure("dry_run", is_winter=False, da_anchor_missing=True)
    assert d.action == "fallback_official_baseline_warn"
    assert d.exit_code == 0


def test_official_baseline_missing_formal_fails():
    d = evaluate_router_failure("formal", official_baseline_missing=True)
    assert d.exit_code == 1


def test_postflight_failure_formal_fails():
    d = evaluate_postflight_failure("formal")
    assert d.exit_code == 1
    assert d.delivery_status == "FAILED_NO_DELIVERY"


def test_postflight_failure_dry_run_partial():
    d = evaluate_postflight_failure("dry_run")
    assert d.status == "PARTIAL"
    assert d.exit_code == 0


def test_export_failure_marks_delivery_failed_but_run_ok():
    d = evaluate_export_failure()
    assert d.status == "COMPLETE"
    assert d.delivery_status == "FAILED_NO_DELIVERY"
    assert d.exit_code == 1


def test_shadow_failure_continues_main_chain():
    d = evaluate_shadow_failure()
    assert d.action == "continue_main_chain"
    assert d.exit_code == 0
    assert d.delivery_status == "NORMAL"
