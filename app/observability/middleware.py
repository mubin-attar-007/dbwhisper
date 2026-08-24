"""ASGI middleware that turns every request into the HTTP metrics declared in ``metrics.py``.

This exists so the cardinality rule is obeyed by construction rather than by everyone remembering
it. The dangerous label is ``route``: recording ``scope["path"]`` would create one time series per
``/schemas/9f3c...`` a user ever visits, and the bounded guard would collapse the metric to
``other`` within an hour. So the template is read back out of the ASGI scope *after* Starlette's
router has matched, which is the only place the templated form (``/schemas/{database_id}``)
actually exists. A request that matched nothing is labelled ``unmatched`` - deliberately one
series for the whole 404 surface, since a 404 path is attacker-controlled by definition.

It is written as raw ASGI rather than ``BaseHTTPMiddleware`` because ``BaseHTTPMiddleware`` wraps
the response in an anyio task group, which changes streaming and exception semantics; a metrics
recorder has no business altering how responses behave.

Failures inside the application still produce a sample: the timer lives in a ``finally`` and an
unhandled exception is recorded as the 500 the server will actually return.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from app.observability.metrics import observe_http_request

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

#: Scrape and probe endpoints. Counting them drowns real traffic in machine traffic, and their
#: latency says nothing about the product.
DEFAULT_EXCLUDED_PATHS: frozenset[str] = frozenset({"/metrics", "/health", "/ready"})

#: One series for every path that matched no route. A 404 path is attacker-controlled.
UNMATCHED_ROUTE = "unmatched"


class MetricsMiddleware:
    """Record request count, status distribution and latency for every handled request."""

    def __init__(self, app: Any, excluded_paths: Iterable[str] | None = None) -> None:
        self.app = app
        self.excluded_paths = (
            frozenset(excluded_paths) if excluded_paths is not None else DEFAULT_EXCLUDED_PATHS
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") in self.excluded_paths:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        # Pre-seeded with 500: if the application raises, that is what the server sends, and the
        # `finally` below would otherwise have no status to record.
        status = {"code": 500}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            observe_http_request(
                method=str(scope.get("method", "GET")),
                route=route_template(scope),
                status_code=status["code"],
                duration_seconds=time.perf_counter() - started,
            )


def route_template(scope: Scope) -> str:
    """The matched route's templated path, or ``unmatched``.

    Starlette puts the matched ``Route`` object into the scope during routing, so this is only
    meaningful once the inner application has run - which is why the call sits in the ``finally``
    rather than at the top of ``__call__``.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


__all__ = ["DEFAULT_EXCLUDED_PATHS", "UNMATCHED_ROUTE", "MetricsMiddleware", "route_template"]
