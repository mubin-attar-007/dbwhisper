"""The one API instrumented code should use, so nobody has to think about tracing being off.

Everything a graph node or service wants to say about its work goes through::

    with span("execute", **{SpanAttr.DIALECT: dialect, SpanAttr.ROW_COUNT: len(rows)}) as s:
        ...
        s.set(**{SpanAttr.LATENCY_MS: elapsed})

and that call behaves identically whether OpenTelemetry is configured or not. When tracing is
disabled the context manager yields a handle whose methods do nothing; call sites carry no
``if tracing_enabled()`` branches, which is the only way instrumentation stays correct over time.

Three decisions are encoded here and each one is a privacy decision, not an ergonomic one:

* **Attributes are scrubbed, always.** Nothing reaches a span without passing the allowlist in
  ``app.observability.attributes``. There is no escape hatch, because an escape hatch is what
  gets used at 2am.
* **Exceptions are recorded by category, never by message.** ``record_exception=False`` is passed
  deliberately: an OTel exception event carries ``exception.message`` and ``exception.stacktrace``,
  and DBWhisper exception messages routinely quote a DSN, a table name, or a value from a row.
  The span gets the error *class* mapped to a closed vocabulary and nothing else.
* **Span names are constrained.** A span name is a grouping dimension in every trace backend, so
  passing the user's question as a name is the same unbounded-cardinality bug as passing it as a
  metric label. Names are normalised to slugs rather than trusted.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.observability.attributes import (
    ErrorCategory,
    ScrubResult,
    SpanAttr,
    classify_exception,
    scrub_attributes,
)
from app.observability.metrics import record_span_attributes_refused
from app.observability.otel import get_tracer

logger = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[a-z][a-z0-9_.\-]{0,63}$")
_NAME_CLEANUP = re.compile(r"[^a-z0-9_.\-]+")
_FALLBACK_NAME = "unnamed"


def normalize_span_name(name: str) -> str:
    """Coerce a span name into a slug, so a caller cannot turn it into free text."""
    if _VALID_NAME.match(name):
        return name
    cleaned = _NAME_CLEANUP.sub("_", name.strip().lower()).strip("_")[:64]
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"{_FALLBACK_NAME}_{cleaned}"[:64] if cleaned else _FALLBACK_NAME
    logger.debug("Normalised span name %r -> %r", name[:40], cleaned)
    return cleaned


@dataclass(slots=True)
class SpanHandle:
    """What ``span()`` yields. Every method is a no-op when tracing is disabled."""

    name: str
    otel_span: Any = None
    started: float = field(default_factory=time.perf_counter)

    @property
    def recording(self) -> bool:
        """True only when a live span is actually collecting."""
        if self.otel_span is None:
            return False
        try:
            return bool(self.otel_span.is_recording())
        except Exception:  # pragma: no cover - defensive
            return False

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def set(self, **attributes: object) -> ScrubResult:
        """Attach attributes, after the allowlist. Returns what was refused, for tests."""
        result = _apply(self.otel_span, attributes)
        return result

    def record_error(
        self, category: ErrorCategory | str, *, exc: BaseException | None = None
    ) -> None:
        """Mark the span failed with a category. The exception object is never serialised."""
        if exc is not None and not isinstance(category, ErrorCategory):
            category = classify_exception(exc)
        value = category.value if isinstance(category, ErrorCategory) else str(category)
        if not self.recording:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            self.otel_span.set_attribute(SpanAttr.ERROR_CATEGORY.value, value)
            # Description is the category, not the message: a Status description is exported.
            self.otel_span.set_status(Status(StatusCode.ERROR, value))
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not record span error", exc_info=True)


def _apply(otel_span: Any, attributes: Mapping[str, object]) -> ScrubResult:
    """Scrub, publish the refusal counts, then set what survived."""
    result = scrub_attributes(attributes)
    record_span_attributes_refused(
        dropped=len(result.dropped),
        rejected=len(result.rejected),
        redacted=len(result.redacted),
    )
    if result.rejected:
        # Loud on purpose: reaching here means instrumentation tried to attach something the
        # denylist names explicitly. It did not leak, but it is a bug worth fixing.
        logger.warning(
            "Span attributes rejected by the observability allowlist: %s",
            ", ".join(sorted(result.rejected)),
        )
    elif result.dropped:
        logger.debug("Span attributes dropped (not allowlisted): %s", ", ".join(result.dropped))

    if otel_span is None or not result.attributes:
        return result
    try:
        for key, value in result.attributes.items():
            otel_span.set_attribute(key, value)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Could not set span attributes", exc_info=True)
    return result


@contextmanager
def span(name: str, **attributes: object) -> Iterator[SpanHandle]:
    """Open a span for a unit of work. Works, and costs almost nothing, with tracing disabled.

    On exit the elapsed time is attached as ``dbw.latency_ms``. On an exception the span is marked
    failed with an :class:`~app.observability.attributes.ErrorCategory` derived from the exception
    *type*, and the exception is re-raised unchanged - instrumentation never swallows an error.
    """
    safe_name = normalize_span_name(name)
    tracer = get_tracer()

    if tracer is None:
        handle = SpanHandle(name=safe_name)
        # The allowlist still runs: a call site that would leak with tracing on is caught by the
        # test suite with tracing off, and the refusal counters stay meaningful either way.
        _apply(None, attributes)
        yield handle
        return

    scrubbed = _apply(None, attributes)
    with tracer.start_as_current_span(
        safe_name,
        attributes=dict(scrubbed.attributes),
        # Both False on purpose - see the module docstring. We set status ourselves, from a
        # category, so no exception message or stack trace is ever exported.
        record_exception=False,
        set_status_on_exception=False,
    ) as otel_span:
        handle = SpanHandle(name=safe_name, otel_span=otel_span)
        try:
            yield handle
        except BaseException as exc:
            handle.record_error(classify_exception(exc))
            _apply(otel_span, {SpanAttr.LATENCY_MS.value: round(handle.elapsed_ms, 3)})
            raise
        _apply(otel_span, {SpanAttr.LATENCY_MS.value: round(handle.elapsed_ms, 3)})


def current_trace_id() -> str | None:
    """The active trace id as 32 hex characters, for stamping onto a persisted run record.

    Returns ``None`` when nothing is being traced, which is the normal case.
    """
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return format(context.trace_id, "032x")
    except Exception:  # pragma: no cover - defensive
        return None


__all__ = [
    "ErrorCategory",
    "SpanAttr",
    "SpanHandle",
    "current_trace_id",
    "normalize_span_name",
    "span",
]
