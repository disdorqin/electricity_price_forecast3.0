"""Command whitelist enforcement.

The backend NEVER executes arbitrary commands. Only a closed set of actions may
run, each mapped to a fixed argv (shell=False), with DB URLs redacted in logs.
"""

import pytest

from backend.app.config import settings
from backend.app.utils.subprocess_runner import (
    ALLOWED_ACTIONS,
    DANGEROUS_ACTIONS,
    build_ops_command,
    run_whitelisted,
)


def test_allowed_actions_closed_set():
    assert ALLOWED_ACTIONS == {
        "init-db",
        "update-data",
        "run-dry-run",
        "run-shadow-monitoring",
        "export-submission",
        "run-formal",
    }
    # No shell escape / deletion / arbitrary exec in the allow-list.
    for banned in ("rm", "sh", "bash", "powershell", "cmd", "sudo", "drop"):
        assert banned not in ALLOWED_ACTIONS


def test_dangerous_actions_marked():
    assert DANGEROUS_ACTIONS == {"export-submission", "run-formal"}


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        build_ops_command("rm -rf /", {"target_date": "2026-01-01"}, db_url="x")
    with pytest.raises(ValueError):
        build_ops_command("DROP TABLE efm_runs", {}, db_url="x")


def test_build_returns_fixed_argv_no_shell_metachars():
    for action in ALLOWED_ACTIONS:
        params = {"target_date": "2026-01-01", "reason": "audit"} if action != "init-db" else {}
        argv = build_ops_command(action, params, db_url="mysql+pymysql://u:p@h:3306/d")
        assert isinstance(argv, list)
        joined = " ".join(argv)
        # No shell operators may ever appear.
        assert ";" not in joined
        assert "|" not in joined
        assert "&&" not in joined
        assert "||" not in joined
        assert "$(" not in joined
        # The dispatch script is always the target; no free-form executable.
        assert argv[1].endswith("ops_dispatch.py")


def test_run_whitelisted_rejects_unknown_action():
    with pytest.raises(ValueError):
        run_whitelisted("arbitrary-command", {}, db_url="mysql+pymysql://u:p@h:3306/d")
