"""Formal/formal_sim mode must FAIL when no day-ahead data exists."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

DB_URL = os.environ.get("EFM3_TEST_DB_URL") or os.environ.get("EFM3_DB_URL", "")


@pytest.mark.skipif(not DB_URL, reason="EFM3_TEST_DB_URL not set — skipping DB-backed tests")
class TestFormalNoDataFails:
    def test_formal_sim_fails_on_no_data(self):
        """formal_sim with no DA ledger data must FAIL (exit != 0)."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-01",  # known no-data date
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] in ("FAIL",), (
            f"Expected FAIL for no-data date, got {result['status']}: "
            f"{json.dumps(result, indent=2, default=str)}"
        )
        assert result["delivery_status"] in ("FAILED_NO_DELIVERY", "DEGRADED_DELIVERED")

    def test_dry_run_does_not_crash_on_no_data(self):
        """dry_run with no data should complete (not crash)."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-01",
            mode="dry_run",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] in ("COMPLETE", "PARTIAL"), (
            f"dry_run should complete, got {result['status']}"
        )

    def test_formal_fails_on_no_data(self):
        """formal mode with no DA ledger data also FAIL."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-01",
            mode="formal",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] in ("FAIL",), (
            f"Formal should FAIL for no-data, got {result['status']}"
        )
