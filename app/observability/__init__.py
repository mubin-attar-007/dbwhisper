"""Observability for DBWhisper: traces, metrics, and the rules about what they may contain.

The package is deliberately small and layered, and the layering is the point:

``attributes.py``  the closed allowlist of span attribute keys and value vocabularies. A leaf
                   module with no application imports, so the privacy rule can be read and tested
                   in isolation.
``metrics.py``     a dedicated Prometheus registry, every metric declared with its label spec, and
                   a cardinality guard that refuses free text as a label value.
``otel.py``        OpenTelemetry setup that is a no-op unless ``OTEL_ENABLED`` and that never
                   fails the application when the collector is unreachable.
``spans.py``       ``with span("execute", ...)`` - the only API instrumented code should call. It
                   works unchanged with tracing on or off.
``middleware.py``  ASGI middleware recording the HTTP metrics, using the matched *route template*
                   so a path with an id in it cannot become a new time series.

Mounting it in the API (``app/main.py``) is three additions::

    from app.observability import metrics as obs_metrics
    from app.observability.middleware import MetricsMiddleware
    from app.observability.otel import setup_tracing

    app.add_middleware(MetricsMiddleware)

    @app.on_event("startup")
    async def _start_observability() -> None:
        setup_tracing(app)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(settings: Settings = Depends(get_settings)) -> Response:
        if not settings.metrics_enabled:
            raise HTTPException(status_code=404, detail="Metrics are disabled")
        payload, content_type = obs_metrics.render()
        return Response(content=payload, media_type=content_type)

Operator-side configuration - collector, Prometheus scrape job, Grafana datasources and two
provisioned dashboards - lives in ``ops/``. The dashboards only reference metrics declared in
``metrics.py``, and ``tests/test_observability.py`` proves it, so a panel cannot quietly go blank
after a metric is renamed.
"""

from app.observability.attributes import (
    ALLOWED_KEYS,
    ErrorCategory,
    ScrubResult,
    SpanAttr,
    classify_exception,
    hash_org_id,
    safe_attributes,
    scrub_attributes,
)
from app.observability.metrics import (
    REGISTRY,
    CacheResult,
    CircuitState,
    HighCardinalityLabelError,
    Outcome,
    PoolState,
    TokenKind,
    check_label,
    metric_catalogue,
    metric_names,
    render,
    safe_label,
    set_build_info,
)
from app.observability.middleware import MetricsMiddleware
from app.observability.otel import (
    TracingStatus,
    setup_tracing,
    shutdown_tracing,
    tracing_enabled,
)
from app.observability.spans import SpanHandle, current_trace_id, span

__all__ = [
    "ALLOWED_KEYS",
    "REGISTRY",
    "CacheResult",
    "CircuitState",
    "ErrorCategory",
    "HighCardinalityLabelError",
    "MetricsMiddleware",
    "Outcome",
    "PoolState",
    "ScrubResult",
    "SpanAttr",
    "SpanHandle",
    "TokenKind",
    "TracingStatus",
    "check_label",
    "classify_exception",
    "current_trace_id",
    "hash_org_id",
    "metric_catalogue",
    "metric_names",
    "render",
    "safe_attributes",
    "safe_label",
    "scrub_attributes",
    "set_build_info",
    "setup_tracing",
    "shutdown_tracing",
    "span",
    "tracing_enabled",
]
