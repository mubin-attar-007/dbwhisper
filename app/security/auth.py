"""API-key authentication for powerful endpoints.

A shared-token scheme (``X-API-Key`` header) sized for protecting the schema-management
endpoints (and optionally ``/query``). Behavior:

* No tokens configured + not required  -> dev-friendly: allow, warn once.
* No tokens configured + required (prod) -> fail closed (503 misconfiguration).
* Tokens configured                     -> require a valid key (401 otherwise).
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings
from app.utils.logger import setup_logging

logger = setup_logging(__name__)

API_KEY_HEADER = "X-API-Key"
_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)
_warned_open = False


def _matches_any(presented: str, allowed: list[str]) -> bool:
    """Constant-time comparison against each configured token."""
    return any(hmac.compare_digest(presented, token) for token in allowed)


async def require_api_key(
    api_key: str | None = Depends(_api_key_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency that gates an endpoint behind the configured API key(s)."""
    tokens = settings.auth_tokens_list
    if not tokens:
        if settings.effective_auth_required:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Server authentication is required but no API keys are configured.",
            )
        global _warned_open
        if not _warned_open:
            logger.warning(
                "No API_AUTH_TOKENS configured; protected endpoints are OPEN (dev mode)."
            )
            _warned_open = True
        return
    if not api_key or not _matches_any(api_key, tokens):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )


async def require_api_key_if_enabled(
    api_key: str | None = Depends(_api_key_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    """Like :func:`require_api_key`, but enforced only when ``gate_query_endpoint`` is set.

    Used on ``/query`` so the public demo stays reachable (rate-limited) until the operator
    opts into gating it.
    """
    if not settings.gate_query_endpoint:
        return
    await require_api_key(api_key=api_key, settings=settings)
