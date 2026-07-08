"""Chain fallback policy — explicit, testable decisions for the 3.0 one-click chain.

This module centralizes the "dry_run vs formal" fallback matrix so the
orchestrator never improvises. Every failure mode maps to a :class:`FallbackDecision`
that prescribes exactly what the orchestrator should do.

The 10-row matrix (see docs/CHAIN_FALLBACK_MATRIX.md):

  1. MySQL unavailable        -> dry_run: FilePredictionStore + PARTIAL; formal: FAIL(1)
  2. Data update failed       -> dry_run: continue if READY dataset exists (DEGRADED_DELIVERED); formal: FAIL(1)
  3. Dataset not READY        -> dry_run: DEGRADED/PARTIAL; formal: FAILED_NO_DELIVERY(1)
  4. DA anchor missing        -> winter dry_run: official_baseline + WARN; winter formal: FAIL unless --allow-router-fallback; non-winter: official_baseline
  5. Official baseline missing-> dry_run: selected-prediction check fails; formal: FAILED_NO_DELIVERY(1)
  6. Shadow module failed     -> main chain continues; shadow DEGRADED, never selected
  7. Postflight fail          -> dry_run: PARTIAL; formal: FAILED_NO_DELIVERY(1)
  8. Export failed            -> run may PASS; delivery_status=FAILED_NO_DELIVERY(1)
  9. Duplicate run_id         -> upsert / new run_id; no duplicate prediction rows
 10. Existing target_date run -> dry_run: new run_id allowed; formal: acquire lock or FAIL
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FallbackDecision:
    """A single fallback decision for one failure mode in one mode."""

    failure: str
    mode: str
    action: str
    status: str
    delivery_status: str
    exit_code: int
    db_enabled: bool
    message: str

    def as_dict(self) -> dict:
        return {
            "failure": self.failure,
            "mode": self.mode,
            "action": self.action,
            "status": self.status,
            "delivery_status": self.delivery_status,
            "exit_code": self.exit_code,
            "db_enabled": self.db_enabled,
            "message": self.message,
        }


def _decide(failure: str, mode: str, *, formal_action: str, dry_action: str,
            status: str, delivery_status: str, exit_code: int, db_enabled: bool,
            message: str) -> FallbackDecision:
    action = formal_action if mode == "formal" else dry_action
    return FallbackDecision(
        failure=failure, mode=mode, action=action,
        status=status, delivery_status=delivery_status,
        exit_code=exit_code, db_enabled=db_enabled, message=message,
    )


def evaluate_db_failure(mode: str, *, allow_router_fallback: bool = False) -> FallbackDecision:
    """Row 1 — MySQL ledger unavailable.

    dry_run : fall back to FilePredictionStore, status=PARTIAL, db_enabled=false.
    formal  : FAIL with exit_code=1 (formal requires the ledger).
    """
    if mode in ("formal", "formal_sim"):
        return _decide(
            "db_unavailable", mode,
            formal_action="fail", dry_action="continue_file_store",
            status="FAIL", delivery_status="FAILED_NO_DELIVERY",
            exit_code=1, db_enabled=False,
            message="Formal/formal_sim run requires MySQL; unavailable -> FAIL (exit 1).",
        )
    return _decide(
        "db_unavailable", mode,
        formal_action="fail", dry_action="continue_file_store",
        status="PARTIAL", delivery_status="PARTIAL",
        exit_code=0, db_enabled=False,
        message="DB unavailable -> FilePredictionStore fallback, status=PARTIAL, db_enabled=false.",
    )


def evaluate_dataset_failure(
    mode: str, *, ready_dataset_exists: bool = False, allow_router_fallback: bool = False,
) -> FallbackDecision:
    """Rows 2 & 3 — data update failed / dataset not READY.

    formal  : always FAIL(1) — a formal delivery needs a READY dataset.
    dry_run : if a READY dataset_version already exists -> DEGRADED_DELIVERED;
              otherwise PARTIAL.
    """
    if mode in ("formal", "formal_sim"):
        return _decide(
            "dataset_not_ready", mode,
            formal_action="fail", dry_action="continue_degraded",
            status="FAIL", delivery_status="FAILED_NO_DELIVERY",
            exit_code=1, db_enabled=True,
            message="Formal/formal_sim run requires a READY dataset; not available -> FAIL (exit 1).",
        )
    if ready_dataset_exists:
        return _decide(
            "dataset_not_ready", mode,
            formal_action="fail", dry_action="continue_degraded",
            status="DEGRADED", delivery_status="DEGRADED_DELIVERED",
            exit_code=0, db_enabled=True,
            message="No fresh data update, but a READY dataset_version exists -> DEGRADED_DELIVERED.",
        )
    return _decide(
        "dataset_not_ready", mode,
        formal_action="fail", dry_action="continue_degraded",
        status="PARTIAL", delivery_status="PARTIAL",
        exit_code=0, db_enabled=True,
        message="Dataset not READY and no fallback available -> PARTIAL.",
    )


def evaluate_router_failure(
    mode: str, *, is_winter: bool = False, da_anchor_missing: bool = False,
    official_baseline_missing: bool = False, allow_router_fallback: bool = False,
) -> FallbackDecision:
    """Rows 4 & 5 — DA anchor missing / official baseline missing.

    DA anchor missing:
      * winter + formal (no --allow-router-fallback): FAIL(1)
      * otherwise: fall back to official_baseline (WARN in winter)
    Official baseline missing:
      * formal: FAILED_NO_DELIVERY(1)
      * dry_run: selected-prediction check fails (PARTIAL)
    """
    if da_anchor_missing:
        if is_winter and mode in ("formal", "formal_sim") and not allow_router_fallback:
            return _decide(
                "da_anchor_missing", mode,
                formal_action="fail", dry_action="fallback_official_baseline_warn",
                status="FAIL", delivery_status="FAILED_NO_DELIVERY",
                exit_code=1, db_enabled=True,
                message="Winter formal/formal_sim with no DA anchor and no --allow-router-fallback -> FAIL (exit 1).",
            )
        return _decide(
            "da_anchor_missing", mode,
            formal_action="fail", dry_action="fallback_official_baseline_warn",
            status="DEGRADED", delivery_status="DEGRADED_DELIVERED",
            exit_code=0, db_enabled=True,
            message="DA anchor missing -> fallback to official_baseline (WARN in winter).",
        )
    if official_baseline_missing:
        if mode in ("formal", "formal_sim"):
            return _decide(
                "official_baseline_missing", mode,
                formal_action="fail", dry_action="fail_selected_check",
                status="FAIL", delivery_status="FAILED_NO_DELIVERY",
                exit_code=1, db_enabled=True,
                message="Official baseline missing -> formal/formal_sim FAILED_NO_DELIVERY (exit 1).",
            )
        return _decide(
            "official_baseline_missing", mode,
            formal_action="fail", dry_action="fail_selected_check",
            status="PARTIAL", delivery_status="PARTIAL",
            exit_code=0, db_enabled=True,
            message="Official baseline missing -> selected-prediction check fails (dry_run PARTIAL).",
        )
    return _decide(
        "router_failure", mode,
        formal_action="fail", dry_action="continue_warn",
        status="DEGRADED", delivery_status="DEGRADED_DELIVERED",
        exit_code=0, db_enabled=True,
        message="Router degraded -> continue with warning.",
    )


def evaluate_postflight_failure(mode: str) -> FallbackDecision:
    """Row 7 — postflight checks failed.

    dry_run : PARTIAL.  formal : FAILED_NO_DELIVERY(1).
    """
    if mode in ("formal", "formal_sim"):
        return _decide(
            "postflight_failed", mode,
            formal_action="fail", dry_action="partial",
            status="FAIL", delivery_status="FAILED_NO_DELIVERY",
            exit_code=1, db_enabled=True,
            message="Postflight failed in formal/formal_sim -> FAILED_NO_DELIVERY (exit 1).",
        )
    return _decide(
        "postflight_failed", mode,
        formal_action="fail", dry_action="partial",
        status="PARTIAL", delivery_status="PARTIAL",
        exit_code=0, db_enabled=True,
        message="Postflight failed in dry_run -> PARTIAL.",
    )


def evaluate_export_failure() -> FallbackDecision:
    """Row 8 — export failed. The run itself may still PASS.

    delivery_status=FAILED_NO_DELIVERY, exit_code=1.
    """
    return FallbackDecision(
        failure="export_failed", mode="any", action="mark_delivery_failed",
        status="COMPLETE", delivery_status="FAILED_NO_DELIVERY",
        exit_code=1, db_enabled=True,
        message="Export failed: run may PASS but delivery_status=FAILED_NO_DELIVERY, exit=1.",
    )


def evaluate_shadow_failure() -> FallbackDecision:
    """Row 6 — a shadow module failed. The main chain continues; the shadow is
    marked DEGRADED and is NEVER selected into the final delivery."""
    return FallbackDecision(
        failure="shadow_failed", mode="any", action="continue_main_chain",
        status="COMPLETE", delivery_status="NORMAL",
        exit_code=0, db_enabled=True,
        message="Shadow module failed: main chain continues; shadow status=DEGRADED, never selected into final.",
    )


def map_to_exit_code(decision: FallbackDecision) -> int:
    """Return the process exit code implied by a fallback decision."""
    return decision.exit_code
