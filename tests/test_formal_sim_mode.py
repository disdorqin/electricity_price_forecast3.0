"""formal_sim is a valid mode and applies formal strict guards."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from cli.parser import build_parser


class TestFormalSimMode:
    def test_formal_sim_is_valid_mode(self):
        """Ensure formal_sim is accepted by CLI parser as a mode choice."""
        p = build_parser()
        mode_action = [a for a in p._actions if a.dest == "mode"][0]
        assert "formal_sim" in mode_action.choices, "formal_sim must be a mode choice"

    def test_formal_sim_is_not_default(self):
        """Default mode should still be dry_run."""
        p = build_parser()
        assert p.get_default("mode") == "dry_run", "Default mode must be dry_run"

    def test_allow_router_fallback_flag_exists(self):
        """--allow-router-fallback should be a CLI flag."""
        p = build_parser()
        for a in p._actions:
            if a.dest == "allow_router_fallback":
                assert a.default is False
                return
        pytest.fail("--allow-router-fallback flag not found in parser")

    def test_formal_sim_runs_with_db(self):
        """formal_sim requires use_db to be meaningful.
        Test that run_full_chain accepts formal_sim mode.
        """
        from pipelines.full_chain_orchestrator import run_full_chain
        from common.fallback_policy import evaluate_db_failure

        # Without DB URL, formal_sim should FAIL fast
        result = run_full_chain(
            target_date="2026-01-25",
            mode="formal_sim",
            use_db=False,
        )
        assert result["status"] in ("FAIL",)
        assert result["delivery_status"] in ("FAILED_NO_DELIVERY",)
        assert result["exit_code"] != 0
