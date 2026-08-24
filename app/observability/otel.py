"""OpenTelemetry wiring, arranged so that tracing can never be the reason the app is down.

Two properties are non-negotiable, and everything in this module exists to guarantee them:

1. **Off by default.** With ``OTEL_ENABLED=false`` - the default - no provider is created, no
   exporter thread starts, and :func:`get_tracer` returns ``None``. The span helper in
   ``app.observability.spans`` then degrades to a no-op object, so instrumented code paths are
   identical whether or not anybody is collecting. No SDK object, exporter, or background thread
   is created; the only cost is importing ``opentelemetry-api``, which starts nothing.
2. **An unreachable collector is a non-event.** Every step of setup is wrapped: a malformed
   endpoint, a missing exporter package, or a collector that is simply not running leaves the
   application serving requests with tracing disabled and one WARNING in the log. Exports run on
   ``BatchSpanProcessor``'s background thread, so a slow collector adds no request latency; the
   export timeout is bounded so a hung one cannot pin that thread forever.

The provider is held in a module-level variable rather than read back from OpenTelemetry's global
because the global can only be set once per process: keeping our own handle lets tests set up and
tear down repeatedly, which is how the allowlist is proved end to end against a real exporter.

Framework spans need their own scrubbing. ``FastAPIInstrumentor`` records ``url.query`` and
``url.full``, and a DBWhisper URL can carry a question or a source identifier in the query string,
so :func:`_scrub_request_hook` overwrites those before the span is ever exported. Our *own* spans
are protected by the allowlist in ``app.observability.attributes``; the collector config in
``ops/otel-collector-config.yaml`` repeats the deletion a third time, outside this process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

logger = logging.getLogger(__name__)

#: Health and metrics endpoints are scraped every few seconds by machines. Tracing them buries the
#: traces that describe actual user work, so they are excluded at the instrumentation layer.
EXCLUDED_URLS = "health,ready,metrics,favicon.ico"

#: Bounded so a hung collector cannot pin the exporter thread indefinitely.
EXPORT_TIMEOUT_SECONDS = 5

#: The OTLP/HTTP exporter wants the full signal path, but operators habitually configure the base
#: URL. Normalise rather than fail on a config mistake nobody would spot until traces went missing.
_TRACES_PATH = "/v1/traces"


@dataclass(frozen=True, slots=True)
class TracingStatus:
    """What setup actually did. Returned so the caller can log it truthfully."""

    enabled: bool
    reason: str
    service_name: str = ""
    exporter: str = "none"
    instrumented_fastapi: bool = False


DISABLED = TracingStatus(enabled=False, reason="otel_enabled=false")

_provider: Any = None
_status: TracingStatus = DISABLED
_global_provider_set = False


def tracing_enabled() -> bool:
    """True when a tracer provider is live in this process."""
    return _provider is not None


def status() -> TracingStatus:
    """The result of the last :func:`setup_tracing` call."""
    return _status


def get_tracer(name: str = "dbwhisper") -> Any:
    """Return a tracer, or ``None`` when tracing is off.

    Callers should not branch on this themselves - use ``app.observability.spans.span``.
    """
    if _provider is None:
        return None
    return _provider.get_tracer(name)


def setup_tracing(
    app: Any = None,
    *,
    settings: Settings | None = None,
    span_processor: Any = None,
) -> TracingStatus:
    """Configure tracing and, when ``app`` is given, instrument FastAPI. Idempotent.

    ``span_processor`` bypasses the OTLP exporter and attaches a caller-supplied processor
    instead. That is how the test suite asserts what really lands on a span: an in-memory
    exporter, the same code path, no network.
    """
    global _provider, _status, _global_provider_set

    settings = settings or _load_settings()
    if settings is None or not settings.otel_enabled:
        _status = DISABLED
        return _status

    if _provider is None:
        try:
            _provider = _build_provider(settings, span_processor)
        except Exception as exc:  # pragma: no cover - defensive: setup must never be fatal
            logger.warning("OpenTelemetry setup failed; continuing without tracing: %s", exc)
            _provider = None
            _status = TracingStatus(enabled=False, reason=f"setup_failed:{type(exc).__name__}")
            return _status

    if app is not None and not _status.instrumented_fastapi:
        _status = replace(_status, instrumented_fastapi=_instrument_fastapi(app))
    return _status


def _build_provider(settings: Settings, span_processor: Any) -> Any:
    """Create the tracer provider and attach exactly one span processor."""
    global _status, _global_provider_set

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            # The mode, not APP_ENV: it is the mode that determines what the app is allowed to do,
            # so it is the dimension worth slicing traces by.
            "deployment.environment": settings.mode.value,
        }
    )
    provider = TracerProvider(resource=resource)

    if span_processor is not None:
        provider.add_span_processor(span_processor)
        exporter_name = "custom"
    elif settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(_otlp_processor(settings.otel_exporter_otlp_endpoint))
        exporter_name = "otlp_http"
    else:
        # Enabled but nowhere to send: spans are still created, which is enough to verify
        # instrumentation locally, and costs one in-memory object per span.
        exporter_name = "none"

    if not _global_provider_set:
        trace.set_tracer_provider(provider)
        _global_provider_set = True

    _status = TracingStatus(
        enabled=True,
        reason="configured",
        service_name=settings.otel_service_name,
        exporter=exporter_name,
    )
    logger.info(
        "OpenTelemetry tracing enabled (service=%s exporter=%s)",
        settings.otel_service_name,
        exporter_name,
    )
    return provider


def _otlp_processor(endpoint: str) -> Any:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    exporter = OTLPSpanExporter(endpoint=_traces_endpoint(endpoint), timeout=EXPORT_TIMEOUT_SECONDS)
    return BatchSpanProcessor(exporter)


def _traces_endpoint(endpoint: str) -> str:
    """Accept either ``http://collector:4318`` or the full ``.../v1/traces`` form."""
    trimmed = endpoint.strip().rstrip("/")
    if trimmed.endswith(_TRACES_PATH):
        return trimmed
    return trimmed + _TRACES_PATH


def _instrument_fastapi(app: Any) -> bool:
    """Attach ASGI instrumentation. Returns whether it was applied."""
    if getattr(app, "_is_instrumented_by_opentelemetry", False):
        return True
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=_provider,
            excluded_urls=EXCLUDED_URLS,
            server_request_hook=_scrub_request_hook,
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("FastAPI instrumentation skipped: %s", exc)
        return False


#: Attributes known to embed the query string, across both the old (``http.*``) and stable
#: (``url.*``) HTTP semantic conventions. The sweep below does not depend on this list being
#: complete - it is here so the common case is handled even if a value arrives re-encoded.
_QUERY_BEARING = ("url.query", "url.full", "http.url", "http.target")

_REDACTED = "[redacted]"


def _scrub_request_hook(span: Any, scope: dict[str, Any]) -> None:
    """Blank out the parts of the framework's HTTP span that can carry user content.

    A DBWhisper URL can carry a question, an email address or a token in its query string, and the
    ASGI instrumentation records the full URL. The instrumentation has already set its attributes
    by the time this hook runs, so the fix is to overwrite them.

    Rather than trusting a fixed list of attribute names - which changes with every semantic
    convention revision - the raw query string is taken from the ASGI scope and *any* string
    attribute containing it is rewritten. That stays correct when the conventions move again.
    """
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    try:
        raw_query = scope.get("query_string") or b""
        if not raw_query:
            return
        query_text = raw_query.decode("utf-8", "replace")
        existing = dict(getattr(span, "attributes", None) or {})
        for key, value in existing.items():
            if isinstance(value, str) and query_text in value:
                span.set_attribute(key, value.replace(query_text, _REDACTED))
        for key in _QUERY_BEARING:
            if key in existing and existing[key] != _REDACTED:
                span.set_attribute(key, _REDACTED)
    except Exception:  # pragma: no cover - a hook must never break a request
        logger.debug("Request-hook scrubbing failed", exc_info=True)


def shutdown_tracing() -> None:
    """Flush and drop the provider. Safe to call when tracing was never started."""
    global _provider, _status

    provider, _provider = _provider, None
    _status = DISABLED
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception:  # pragma: no cover - defensive
        logger.debug("Tracer provider shutdown failed", exc_info=True)


def _load_settings() -> Settings | None:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:  # pragma: no cover - defensive
        logger.debug("Could not load settings; tracing stays disabled", exc_info=True)
        return None


__all__ = [
    "DISABLED",
    "EXCLUDED_URLS",
    "EXPORT_TIMEOUT_SECONDS",
    "TracingStatus",
    "get_tracer",
    "setup_tracing",
    "shutdown_tracing",
    "status",
    "tracing_enabled",
]
