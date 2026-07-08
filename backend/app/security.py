"""
Security guards.

1. API-key / localhost guard — when EFM3_API_KEY is set, non-localhost requests
   must present a matching ``X-API-Key`` header.
2. Ops guard — ops endpoints are disabled unless ``ops_enabled`` is true, except
   from localhost. Dangerous ops (formal / export) additionally require
   ``confirm=true`` (enforced in the ops router/service, not here).

The DB password is NEVER returned by any endpoint and is redacted from logs.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from .config import settings


def client_host(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def is_local(host: str) -> bool:
    return host in settings.ops_allow_from_list


async def require_access(request: Request) -> None:
    """Allow if no API key is configured (local-first dev mode), or if a valid
    key is presented, or if the caller is localhost."""
    if not settings.api_key:
        return
    key = request.headers.get("X-API-Key", "")
    if key and key == settings.api_key:
        return
    if is_local(client_host(request)):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid API key for non-localhost access.",
    )


async def require_ops(request: Request) -> None:
    """Ops endpoints are available ONLY when ``ops_enabled`` is true.

    When disabled (the default), EVERY ``/api/ops/*`` request returns 403,
    regardless of origin (no localhost bypass, by design — ops trigger real
    pipeline side effects and must never run silently). When enabled, access
    still requires a valid API key or localhost.
    """
    if not settings.ops_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ops endpoints are disabled (EFM3_OPS_ENABLED=false). "
            "Enable ops and provide a valid key to use them.",
        )
    await require_access(request)


def assert_confirm(confirm: bool, action: str) -> None:
    """Dangerous operations must be explicitly confirmed."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Operation '{action}' requires explicit confirm=true. "
            f"This is a production/export action and must not be triggered silently.",
        )
