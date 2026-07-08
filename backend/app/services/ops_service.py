"""Ops service — triggers whitelisted pipeline operations via subprocess.

Safety:
  * Only actions in ALLOWED_ACTIONS may run (enforced here + in subprocess_runner).
  * Dangerous actions (formal / export) require explicit confirm=true.
  * A per-target_date lock prevents concurrent formal runs for the same date.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from ..config import settings
from ..utils.subprocess_runner import ALLOWED_ACTIONS, DANGEROUS_ACTIONS, run_whitelisted

_lock = threading.Lock()
_formal_locks: Dict[str, float] = {}


class OpsError(Exception):
    pass


def run_action(action: str, params: Dict[str, Any], client_host: str = "") -> Dict[str, Any]:
    """Execute a whitelisted op. Raises OpsError on guard violations."""
    if action not in ALLOWED_ACTIONS:
        raise OpsError(f"Action '{action}' not permitted. Allowed: {sorted(ALLOWED_ACTIONS)}")

    # Double-guard: dangerous ops must be confirmed (router also enforces this).
    if action in DANGEROUS_ACTIONS and not params.get("confirm"):
        raise OpsError(f"Operation '{action}' requires confirm=true.")

    if action == "run-formal":
        td = params.get("target_date") or ""
        with _lock:
            if td in _formal_locks:
                raise OpsError(f"A formal run for {td} is already in progress.")
            _formal_locks[td] = time.time()
        try:
            return _execute(action, params)
        finally:
            with _lock:
                _formal_locks.pop(td, None)

    return _execute(action, params)


def _execute(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    started = time.time()
    result = run_whitelisted(action, params, db_url=settings.db_url)
    result["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started))
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    return result
