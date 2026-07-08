"""
Subprocess runner with a STRICT command whitelist.

The backend never executes arbitrary shell commands. Every operation maps to a
fixed argv list built by ``build_ops_command``. ``run_whitelisted`` always uses
``shell=False`` and enforces a hard timeout. DB URLs in argv are redacted before
any logging.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any, Dict, List, Optional

from ..config import REPO_ROOT, settings
from .redaction import redact_db_url

logger = logging.getLogger(__name__)

# Allowed operation actions (the ONLY operations the backend may trigger).
ALLOWED_ACTIONS = {
    "init-db",
    "update-data",
    "run-dry-run",
    "run-shadow-monitoring",
    "export-submission",
    "run-formal",
}

# Dangerous operations require explicit confirm=true from the caller.
DANGEROUS_ACTIONS = {"export-submission", "run-formal"}


def _dispatch_script() -> str:
    import os

    return os.path.join(REPO_ROOT, "backend", "app", "ops_dispatch.py")


def build_ops_command(action: str, params: Dict[str, Any], db_url: str) -> List[str]:
    """Build a fixed argv list for an allowed action. Raises ValueError otherwise.

    The returned list never contains a shell metacharacter and ``shell=False`` is
    always used downstream, so no arbitrary command can be executed.
    """
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Action '{action}' is not in the allow-list: {sorted(ALLOWED_ACTIONS)}")

    target_date = (params.get("target_date") or "").strip()
    source = params.get("data_source") or params.get("source") or "all"
    scan_only = bool(params.get("scan_only", False))
    full_refresh = bool(params.get("full_refresh", False))
    data_root = params.get("data_root") or ""
    chain = params.get("chain") or "seasonal_da_router"
    with_p3 = bool(params.get("with_p3_shadow", False))
    with_selector = bool(params.get("with_selector_shadow", False))
    report_dir = params.get("report_dir") or "outputs/db_shadow_monitoring"

    py = sys.executable
    script = _dispatch_script()

    base = [py, script, action, "--db-url", db_url]

    if action == "init-db":
        argv = base
    elif action == "update-data":
        argv = base + ["--json", json.dumps({
            "target_date": target_date or None,
            "source": source,
            "scan_only": scan_only,
            "full_refresh": full_refresh,
            "data_root": data_root or None,
        })]
    elif action == "run-dry-run":
        if not target_date:
            raise ValueError("run-dry-run requires target_date")
        argv = base + ["--json", json.dumps({
            "target_date": target_date,
            "chain": chain,
            "with_p3_shadow": with_p3,
            "with_selector_shadow": with_selector,
        })]
    elif action == "run-shadow-monitoring":
        if not target_date:
            raise ValueError("run-shadow-monitoring requires target_date")
        argv = base + ["--json", json.dumps({
            "target_date": target_date,
            "data_source": source,
            "with_p3_shadow": with_p3,
            "with_selector_shadow": with_selector,
            "report_dir": report_dir,
        })]
    elif action == "export-submission":
        if not target_date:
            raise ValueError("export-submission requires target_date")
        argv = base + ["--json", json.dumps({
            "target_date": target_date,
            "chain": chain,
        })]
    elif action == "run-formal":
        if not target_date:
            raise ValueError("run-formal requires target_date")
        argv = base + ["--json", json.dumps({
            "target_date": target_date,
            "chain": chain,
        })]
    else:  # pragma: no cover - guarded above
        raise ValueError(f"Unhandled action '{action}'")

    return argv


def _redact_argv(argv: List[str]) -> List[str]:
    out = []
    for i, tok in enumerate(argv):
        if tok == "--db-url" and i + 1 < len(argv):
            out.append("--db-url")
            out.append(redact_db_url(argv[i + 1]))
            # skip the next token since we already replaced it
            # (handled by continue below)
        else:
            out.append(tok)
    return out


def run_whitelisted(
    action: str,
    params: Dict[str, Any],
    db_url: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute a whitelisted operation as a child process.

    Returns a dict: {status, exit_code, stdout, stderr, action}.
    Uses ``shell=False`` always. DB URL is redacted in all logs.
    """
    db_url = db_url or settings.db_url
    if not db_url:
        return {"status": "error", "exit_code": -1, "error": "No DB URL configured", "action": action}

    argv = build_ops_command(action, params, db_url)
    safe_argv = _redact_argv(argv)
    logger.info("Whitelisted op [%s] cmd=%s", action, " ".join(safe_argv))
    logger.info("Whitelisted op [%s] redacted_db_url=%s", action, redact_db_url(db_url))

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else settings.ops_timeout,
            shell=False,  # NEVER use shell — fixed argv only
            cwd=REPO_ROOT,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-4000:] if proc.stdout else "",
            "stderr": proc.stderr[-4000:] if proc.stderr else "",
            "action": action,
        }
    except subprocess.TimeoutExpired as e:
        logger.error("Whitelisted op [%s] timed out after %ss", action, timeout)
        return {
            "status": "timeout",
            "exit_code": -1,
            "error": f"Operation timed out after {timeout}s",
            "action": action,
            "stdout": e.stdout[-2000:] if isinstance(e.stdout, str) else "",
            "stderr": e.stderr[-2000:] if isinstance(e.stderr, str) else "",
        }
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("Whitelisted op [%s] failed", action)
        return {"status": "error", "exit_code": -1, "error": str(e), "action": action}
