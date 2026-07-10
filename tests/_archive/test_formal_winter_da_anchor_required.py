"""Formal/formal_sim mode: winter months MUST have DA anchor rows."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

DB_URL = os.environ.get("EFM3_TEST_DB_URL") or os.environ.get("EFM3_DB_URL", "")


@pytest.mark.skipif(not DB_URL, reason="EFM3_TEST_DB_URL not set — skipping DB-backed tests")
class TestFormalWinterDaAnchor:
    def test_formal_sim_winter_good_date_passes(self):
        """Winter date with da_anchor should PASS formal_sim."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-25",  # winter, has da_anchor
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] in ("COMPLETE", "PARTIAL"), (
            f"Expected COMPLETE for winter good date, got {result['status']}"
        )

    def test_formal_sim_winter_no_da_anchor_fails(self):
        """Winter date without da_anchor must FAIL in formal_sim."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-15",  # winter, NO da_anchor
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] == "FAIL", (
            f"Winter no-da_anchor should FAIL in formal_sim, got {result['status']}"
        )

    def test_formal_sim_winter_no_da_anchor_allowed_with_flag(self):
        """Winter without da_anchor with allow_router_fallback avoids da_anchor FAIL,
        but final_selected/fusion still fail (0 rows total) so overall is FAIL."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-15",
            mode="formal_sim",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={"allow_router_fallback": True},
        )
        # Even with router fallback, 0 predictions means final_selected=0 → FAIL
        assert result["status"] == "FAIL", (
            f"Expected FAIL (0 predictions), got {result['status']}"
        )

    def test_formal_winter_no_da_anchor_fails(self):
        """Formal mode winter without da_anchor must FAIL."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-15",
            mode="formal",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] == "FAIL", (
            f"Formal winter no-da_anchor should FAIL, got {result['status']}"
        )

    def test_dry_run_winter_no_da_anchor_does_not_crash(self):
        """dry_run winter without da_anchor should complete (no crash)."""
        from pipelines.full_chain_orchestrator import run_full_chain

        result = run_full_chain(
            target_date="2026-01-15",
            mode="dry_run",
            use_db=True,
            db_url=DB_URL,
            export_submission=False,
            config={},
        )
        assert result["status"] in ("COMPLETE", "PARTIAL"), (
            f"dry_run should not crash, got {result['status']}"
        )
