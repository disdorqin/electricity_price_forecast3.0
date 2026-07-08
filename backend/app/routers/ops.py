"""Ops endpoints — trigger whitelisted pipeline operations.

Safety:
  * All routes require Depends(require_ops) (key/local + ops_enabled unless localhost).
  * Dangerous operations (export-submission / formal) require confirm=true in the
    request body AND are double-guarded in ops_service. The backend NEVER runs them
    silently.
  * No free-form command is accepted; only ALLOWED_ACTIONS reach subprocess_runner.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..config import settings
from ..security import assert_confirm, require_ops
from ..services import ops_service
from ..schemas.ops import OpRequest, OpResponse

router = APIRouter(prefix="/api/ops", tags=["ops"], dependencies=[Depends(require_ops)])


def _host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _assert_dangerous(req: OpRequest, action: str) -> None:
    """Dangerous ops require explicit confirm=true AND a non-empty reason."""
    assert_confirm(req.confirm, action)
    if not req.reason or not req.reason.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Operation '{action}' requires a non-empty 'reason' for audit.",
        )


def _run(action: str, req: OpRequest, request: Request) -> Dict[str, Any]:
    if not settings.db_url:
        raise HTTPException(HTTP_503_SERVICE_UNAVAILABLE, "Database URL not configured (set EFM3_DB_URL)")
    params = req.model_dump()
    try:
        result = ops_service.run_action(action, params, client_host=_host(request))
    except ops_service.OpsError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/init-db", response_model=OpResponse)
def init_db(req: OpRequest, request: Request) -> Dict[str, Any]:
    return _run("init-db", req, request)


@router.post("/update-data", response_model=OpResponse)
def update_data(req: OpRequest, request: Request) -> Dict[str, Any]:
    return _run("update-data", req, request)


@router.post("/run-dry-run", response_model=OpResponse)
def run_dry_run(req: OpRequest, request: Request) -> Dict[str, Any]:
    if not req.target_date:
        raise HTTPException(400, "run-dry-run requires target_date")
    return _run("run-dry-run", req, request)


@router.post("/run-shadow-monitoring", response_model=OpResponse)
def run_shadow_monitoring(req: OpRequest, request: Request) -> Dict[str, Any]:
    if not req.target_date:
        raise HTTPException(400, "run-shadow-monitoring requires target_date")
    return _run("run-shadow-monitoring", req, request)


@router.post("/export-submission", response_model=OpResponse)
def export_submission(req: OpRequest, request: Request) -> Dict[str, Any]:
    _assert_dangerous(req, "export-submission")
    if not req.target_date:
        raise HTTPException(400, "export-submission requires target_date")
    return _run("export-submission", req, request)


@router.post("/run-formal", response_model=OpResponse)
def run_formal(req: OpRequest, request: Request) -> Dict[str, Any]:
    _assert_dangerous(req, "run-formal")
    if not req.target_date:
        raise HTTPException(400, "run-formal requires target_date")
    return _run("run-formal", req, request)
