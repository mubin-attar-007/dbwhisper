"""Per-IP HTTP rate-limiting middleware (in-memory token bucket).

Reuses the process-local token bucket in ``app.utils.tool_cache``. Suitable for a single
instance (e.g. one Hugging Face Space). For multi-instance deployments, swap
``get_rate_limiter`` for an Upstash/Redis-backed limiter behind the same interface.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.utils.logger import setup_logging
from app.utils.tool_cache import get_rate_limiter

logger = setup_logging(__name__)

# Paths that must never be rate-limited (probes, docs, landing).
_EXEMPT = ("/health", "/ready", "/docs", "/openapi.json", "/redoc", "/static")


def _is_exempt(path: str) -> bool:
    if path == "/":
        return True
    return any(path == p or path.startswith(p + "/") for p in _EXEMPT)


def _client_ip(request: Request) -> str:
    """Resolve the real client IP behind proxies (Vercel → HF Space).

    Trusts X-Forwarded-For / X-Real-IP since the app sits behind known proxies; falls back
    to the socket peer. Without this, per-IP limiting would bucket all traffic under the
    proxy's single IP.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket limiter keyed per (client IP, path); tighter on ``/query``."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        path = request.url.path
        if not settings.rate_limit_enabled or _is_exempt(path):
            return await call_next(request)

        client_ip = _client_ip(request)
        if path.startswith("/query"):
            capacity, refill = settings.query_rate_limit_burst, settings.query_rate_limit_per_sec
        else:
            capacity, refill = settings.rate_limit_burst, settings.rate_limit_per_sec

        limiter = get_rate_limiter(
            f"http:{client_ip}:{path}", capacity=capacity, refill_rate=refill
        )
        if not limiter.allow():
            logger.warning("Rate limit exceeded ip=%s path=%s", client_ip, path)
            return JSONResponse(
                status_code=429,
                media_type="application/problem+json",
                content={
                    "type": "about:blank#rate-limited",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": "Rate limit exceeded. Please retry later.",
                },
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
