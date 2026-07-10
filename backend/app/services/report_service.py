"""Report service — shadow safety, DB health, and report references."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pymysql.connections import Connection

from ..config import REPO_ROOT, settings
from .base import q_one, q_scalar

_SHADOW_STAGES = ("selector_shadow", "p3_shadow", "extreme_price_shadow", "shadow")


def shadow_safety(conn: Connection) -> Dict[str, Any]:
    """Compute shadow-safety metrics. Prefers the dashboard view, falls back."""
    try:
        row = q_one(conn, "SELECT * FROM v_efm_shadow_safety")
        if row:
            unsafe = int(row.get("unsafe_run_count", 0) or 0)
            return {
                "status": "SAFE" if unsafe == 0 else "UNSAFE",
                "shadow_selected_count": int(row.get("shadow_selected_count", 0) or 0),
                "final_from_shadow_count": int(row.get("final_from_shadow_count", 0) or 0),
                "unsafe_run_count": unsafe,
                "source": "view",
            }
    except Exception:
        pass

    shadow_selected = q_scalar(
        conn, "SELECT COUNT(*) FROM efm_predictions WHERE is_shadow=1 AND is_selected=1"
    ) or 0
    final_from_shadow = q_scalar(
        conn,
        "SELECT COUNT(*) FROM efm_predictions p "
        "JOIN efm_dim_stage s ON p.stage_id = s.id "
        "WHERE p.is_selected=1 AND s.name IN %s",
        (_SHADOW_STAGES,),
    ) or 0
    unsafe = q_scalar(
        conn,
        "SELECT COUNT(*) FROM efm_runs WHERE status='FAIL' OR delivery_status='FAILED_NO_DELIVERY'",
    ) or 0
    return {
        "status": "SAFE" if int(unsafe) == 0 else "UNSAFE",
        "shadow_selected_count": int(shadow_selected),
        "final_from_shadow_count": int(final_from_shadow),
        "unsafe_run_count": int(unsafe),
        "source": "computed",
    }


def db_health(conn: Connection) -> Dict[str, Any]:
    """Return table list and count from the live connection."""
    from common.db.schema import list_tables

    tables = list_tables(conn)
    return {
        "status": "ok",
        "db_url_prefix": settings.db_url.split("@")[-1].split("/")[0] if settings.db_url else "",
        "table_count": len(tables),
        "tables": tables,
    }


def latest_report() -> Dict[str, Any]:
    path = "docs/experiments/db_ops/DB_CHAIN_RELEASE_CANDIDATE_REPORT.md"
    import os

    full = os.path.join(REPO_ROOT, path)
    return {"name": "db_rc", "available": os.path.exists(full), "path": path}


def run_report(run_id: str) -> Dict[str, Any]:
    return {
        "name": f"run_{run_id}",
        "available": True,
        "path": f"outputs/db_dry_run/{run_id}/" if run_id else None,
    }


def available_reports() -> List[Dict[str, Any]]:
    return [latest_report()]
