"""
EFM3 ops dispatch — the single, audited child process the backend may spawn.

The backend builds a fixed argv (see utils/subprocess_runner.py) that always
points at THIS file with an allowed ``action``. This module maps each action to a
specific pipeline function. It accepts NO free-form command, so the backend can
never execute arbitrary code.

Usage (invoked by the backend, not by users directly):
    python backend/app/ops_dispatch.py <action> --db-url <url> --json <params-json>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Dict

# Ensure the EFM3 repository root is importable (backend/app/ops_dispatch.py -> repo root).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from common.db.connection import DbConnectionManager  # noqa: E402
from common.db.schema import init_schema, list_tables  # noqa: E402


def _ok(payload: Dict[str, Any]) -> int:
    print(json.dumps({"status": "ok", **payload}, default=str))
    return 0


def _fail(msg: str, payload: Dict[str, Any] | None = None) -> int:
    print(json.dumps({"status": "error", "message": msg, **(payload or {})}, default=str))
    return 1


def cmd_init_db(db_url: str) -> int:
    mgr = DbConnectionManager(db_url=db_url)
    conn = mgr.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("CREATE DATABASE IF NOT EXISTS efm3 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute("USE efm3")
        cur.close()
        conn.commit()
        res = init_schema(conn)
        tables = list_tables(conn)
        mgr.close()
        return _ok({"statements_executed": res["statements_executed"], "table_count": len(tables), "tables": tables})
    except Exception as e:  # pragma: no cover - defensive
        return _fail(f"init-db failed: {e}", {"detail": traceback.format_exc()})


def cmd_update_data(db_url: str, params: Dict[str, Any]) -> int:
    from pipelines.data_update_pipeline import run_data_update

    result = run_data_update(
        target_date=params.get("target_date"),
        source=params.get("source", "all"),
        scan_only=bool(params.get("scan_only", False)),
        full_refresh=bool(params.get("full_refresh", False)),
        data_root=params.get("data_root"),
        db_url=db_url,
    )
    return _ok(result)


def cmd_dry_run(db_url: str, params: Dict[str, Any]) -> int:
    from pipelines.full_chain_orchestrator import run_full_chain

    result = run_full_chain(
        target_date=params["target_date"],
        mode="dry_run",
        use_db=True,
        db_url=db_url,
        export_submission=False,
        export_report=False,
        config={
            "enable_p3_shadow": bool(params.get("with_p3_shadow", False)),
            "enable_selector_shadow": bool(params.get("with_selector_shadow", False)),
        },
    )
    return _ok(result)


def cmd_shadow_monitoring(db_url: str, params: Dict[str, Any]) -> int:
    from scripts.run_daily_shadow_monitoring import run_daily_shadow_monitoring

    result = run_daily_shadow_monitoring(
        target_date=params["target_date"],
        db_url=db_url,
        data_source=params.get("data_source", "all"),
        with_p3_shadow=bool(params.get("with_p3_shadow", False)),
        with_selector_shadow=bool(params.get("with_selector_shadow", False)),
        report_dir=params.get("report_dir", "outputs/db_shadow_monitoring"),
    )
    return _ok(result)


def cmd_export_submission(db_url: str, params: Dict[str, Any]) -> int:
    from pipelines.full_chain_orchestrator import run_full_chain

    # Formal mode + explicit submission export. Only reached after the backend
    # has verified confirm=true. Never silent.
    result = run_full_chain(
        target_date=params["target_date"],
        mode="formal",
        use_db=True,
        db_url=db_url,
        export_submission=True,
        export_report=True,
        config={},
    )
    return _ok(result)


def cmd_formal(db_url: str, params: Dict[str, Any]) -> int:
    from pipelines.full_chain_orchestrator import run_full_chain

    result = run_full_chain(
        target_date=params["target_date"],
        mode="formal",
        use_db=True,
        db_url=db_url,
        export_submission=False,
        export_report=True,
        config={},
    )
    return _ok(result)


ACTION_HANDLERS = {
    "init-db": cmd_init_db,
    "update-data": cmd_update_data,
    "run-dry-run": cmd_dry_run,
    "run-shadow-monitoring": cmd_shadow_monitoring,
    "export-submission": cmd_export_submission,
    "run-formal": cmd_formal,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EFM3 ops dispatch (backend-internal)")
    parser.add_argument("action", choices=sorted(ACTION_HANDLERS.keys()))
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--json", default="{}", help="JSON-encoded parameters")
    args = parser.parse_args(argv)

    try:
        params = json.loads(args.json) if args.json else {}
    except json.JSONDecodeError as e:
        return _fail(f"Invalid --json payload: {e}")

    try:
        handler = ACTION_HANDLERS[args.action]
        if args.action == "init-db":
            return handler(args.db_url)
        return handler(args.db_url, params)
    except Exception as e:  # pragma: no cover - defensive
        return _fail(f"{args.action} failed: {e}", {"detail": traceback.format_exc()})


if __name__ == "__main__":
    raise SystemExit(main())
