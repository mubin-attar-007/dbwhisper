"""Prometheus instrumentation, and the cardinality rule that keeps it affordable.

Prometheus creates one time series per unique label combination and keeps it in memory for the
retention window. That makes label choice an operational decision, not a formatting one: a single
label carrying user input is the difference between a metric with forty series and one that kills
the scrape target. The rule this module enforces is therefore:

    **A label value must come from a set bounded by the code, never by user input.**

Concretely: no SQL, no question text, no user id, no organisation id, no connection string, no
table name, no file path, and no free-form error message may ever be a label value. Those belong
in a run record or (hashed, allowlisted) on a span - see ``app.observability.attributes``.

Two kinds of label are permitted, both described by :class:`LabelSpec` and both enforced at the
recording boundary by :func:`safe_label`:

* **closed** - the value must be one of a literal set (``outcome``, ``decision``, ``state``).
  A value outside the set is a bug in the caller and is refused.
* **bounded** - the population is not known ahead of time but is small and operator-controlled
  (``provider``, ``model``, ``route``). Values must look like slugs, and the first ``max_values``
  distinct ones are admitted; anything beyond that collapses to ``other``. The metric degrades
  rather than exploding, and the collapse is itself counted.

Quantiles are deliberately *not* computed here. p50/p95/p99 come from ``histogram_quantile`` over
the ``_bucket`` series in Prometheus, because a quantile computed per-process cannot be aggregated
across replicas. What this module ships is the bucket boundaries; the dashboards in ``ops/`` do
the arithmetic.

The collectors live in a dedicated :class:`CollectorRegistry` rather than the process-global
default so that importing this module never mutates global state a test might depend on, and so
``render()`` returns exactly what we declared.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

from app.observability.attributes import ErrorCategory

logger = logging.getLogger(__name__)

#: Value substituted when a bounded label runs out of room. Chosen so a dashboard shows the
#: collapse instead of silently losing the series.
OTHER = "other"

#: Placeholder for "this sample describes a success", so success and failure share one metric.
NO_ERROR = "none"

#: Longest permitted bounded label value. Anything longer is prose, not an identifier.
MAX_LABEL_CHARS = 64

#: A bounded label value has to look like an identifier a human wrote *in code*: a route template
#: (``/schemas/{id}``), a profile name (``local-bge-small``), a version (``sql_policy@2.0.0``).
_LABEL_SLUG = re.compile(r"^[A-Za-z0-9_/{][A-Za-z0-9._:/@{}\[\]-]*$")

#: Shapes that prove a value is *not* an identifier, checked before the slug pattern so the error
#: message can name the rule that fired. Whitespace and quotes catch a question or a SQL statement,
#: ``://`` catches a connection string, ``=`` catches a ``key=secret`` pair.
_NOT_AN_IDENTIFIER = re.compile(r"[\s'\"`;=*]|://")


class HighCardinalityLabelError(ValueError):
    """Raised by :func:`check_label` when a value would create unbounded series.

    The runtime path (:func:`safe_label`) never raises - it degrades to ``other`` - so this
    exception is a development and test signal, not a production failure mode.
    """


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """How one label name is constrained. ``allowed`` set means closed, otherwise bounded."""

    name: str
    allowed: frozenset[str] | None = None
    max_values: int = 0

    @property
    def is_closed(self) -> bool:
        return self.allowed is not None


# --- Closed vocabularies -------------------------------------------------------------------------
# These are metric *label* vocabularies. ErrorCategory is shared with spans and lives in
# app.observability.attributes so trace and metric error taxonomies cannot drift apart.


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


class CacheResult(StrEnum):
    HIT = "hit"
    MISS = "miss"


class TokenKind(StrEnum):
    PROMPT = "prompt"
    COMPLETION = "completion"


class CircuitState(StrEnum):
    """Mirrors ``app.llm.router.BreakerState``.

    Duplicated rather than imported so that scraping ``/metrics`` does not drag in the model
    layer (and, through it, httpx and the profile registry). ``tests/test_observability.py``
    asserts the two enums stay identical, which turns the duplication into a checked mirror.
    """

    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"

    @property
    def code(self) -> int:
        """Numeric encoding for the gauge: 0 closed, 1 half-open, 2 open (higher is worse)."""
        return {CircuitState.CLOSED: 0, CircuitState.HALF_OPEN: 1, CircuitState.OPEN: 2}[self]


class PoolState(StrEnum):
    IN_USE = "in_use"
    IDLE = "idle"
    OVERFLOW = "overflow"


#: Mirrors ``app.sqlpolicy.Dialect``; checked against it in the test suite.
_DIALECTS = frozenset({"postgres", "mysql", "tsql", "sqlite", "duckdb", "generic"})
#: Mirrors ``app.sqlpolicy.PolicyLevel``; checked against it in the test suite.
_POLICY_LEVELS = frozenset({"standard", "strict", "advanced", "admin_reviewed"})
#: Mirrors ``app.sqlpolicy.Decision``; checked against it in the test suite.
_POLICY_DECISIONS = frozenset({"allow", "deny", "needs_approval"})
#: Mirrors ``app.platform.modes.AppMode``; checked against it in the test suite.
_APP_MODES = frozenset({"demo", "self_hosted", "production"})

#: Every status code the API is expected to return. An unexpected code collapses to ``other``
#: rather than opening the door to arbitrary integers.
_STATUS_CODES = frozenset(
    {
        "200",
        "201",
        "202",
        "204",
        "301",
        "302",
        "304",
        "400",
        "401",
        "403",
        "404",
        "405",
        "409",
        "413",
        "422",
        "429",
        "500",
        "502",
        "503",
        "504",
    }
)

_LABEL_SPECS: dict[str, LabelSpec] = {
    # Closed: the value set is fixed by code or by the HTTP spec.
    "method": LabelSpec(
        "method",
        allowed=frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}),
    ),
    "status": LabelSpec("status", allowed=_STATUS_CODES),
    "outcome": LabelSpec("outcome", allowed=frozenset(o.value for o in Outcome)),
    "result": LabelSpec("result", allowed=frozenset(r.value for r in CacheResult)),
    "kind": LabelSpec("kind", allowed=frozenset(k.value for k in TokenKind)),
    "state": LabelSpec(
        "state",
        allowed=frozenset({s.value for s in CircuitState} | {s.value for s in PoolState}),
    ),
    "disposition": LabelSpec("disposition", allowed=frozenset({"dropped", "rejected", "redacted"})),
    "dialect": LabelSpec("dialect", allowed=_DIALECTS),
    "level": LabelSpec("level", allowed=_POLICY_LEVELS),
    "decision": LabelSpec("decision", allowed=_POLICY_DECISIONS),
    "mode": LabelSpec("mode", allowed=_APP_MODES),
    # "none" is part of the closed set so a success and a failure share one metric: filtering on
    # error_category="none" is how a dashboard separates them without a second time series.
    "error_category": LabelSpec(
        "error_category", allowed=frozenset({c.value for c in ErrorCategory} | {NO_ERROR})
    ),
    # Bounded: operator-controlled populations. Caps are generous but finite.
    "route": LabelSpec("route", max_values=64),
    "provider": LabelSpec("provider", max_values=24),
    "from_provider": LabelSpec("from_provider", max_values=24),
    "to_provider": LabelSpec("to_provider", max_values=24),
    "model": LabelSpec("model", max_values=48),
    "capability": LabelSpec("capability", max_values=24),
    "stage": LabelSpec("stage", max_values=24),
    "rule": LabelSpec("rule", max_values=64),
    "reason": LabelSpec("reason", max_values=48),
    "cache": LabelSpec("cache", max_values=12),
    "queue": LabelSpec("queue", max_values=12),
    "suite": LabelSpec("suite", max_values=24),
    "policy_version": LabelSpec("policy_version", max_values=8),
}

# The self-observation counter is labelled by *label name*, and label names are exactly the keys
# above - a closed set by construction. Declared after the dict so it stays in sync automatically.
_LABEL_SPECS["label"] = LabelSpec("label", allowed=frozenset(_LABEL_SPECS) | {"label", OTHER})

_ADMITTED: dict[str, set[str]] = {}
_ADMISSION_LOCK = threading.Lock()


def check_label(label: str, value: object) -> str:
    """Validate one label value against its spec, raising on anything unbounded.

    Used by tests and by any caller that would rather fail loudly than silently degrade.
    """
    spec = _LABEL_SPECS.get(label)
    if spec is None:
        raise HighCardinalityLabelError(
            f"Unknown metric label '{label}'. Add a LabelSpec before using it."
        )
    text = value.value if isinstance(value, StrEnum) else str(value)
    if spec.is_closed:
        assert spec.allowed is not None  # narrowed by is_closed
        if text not in spec.allowed:
            raise HighCardinalityLabelError(
                f"Label '{label}' is closed; '{_preview(text)}' is not one of its "
                f"{len(spec.allowed)} permitted values."
            )
        return text
    if len(text) > MAX_LABEL_CHARS:
        raise HighCardinalityLabelError(
            f"Label '{label}' value is {len(text)} characters (max {MAX_LABEL_CHARS}); "
            f"'{_preview(text)}' is prose, not an identifier."
        )
    if _NOT_AN_IDENTIFIER.search(text) or not _LABEL_SLUG.match(text):
        raise HighCardinalityLabelError(
            f"Label '{label}' must be a code-defined identifier; got '{_preview(text)}'. "
            f"Free text - SQL, a question, a connection string - is never a label value."
        )
    with _ADMISSION_LOCK:
        seen = _ADMITTED.setdefault(label, set())
        if text in seen:
            return text
        if len(seen) >= spec.max_values:
            raise HighCardinalityLabelError(
                f"Label '{label}' already holds {len(seen)} distinct values (cap "
                f"{spec.max_values}); '{_preview(text)}' would grow the series set further."
            )
        seen.add(text)
    return text


def safe_label(label: str, value: object) -> str:
    """Runtime path: return a value that is safe to use, never raising.

    A refusal is recorded on ``dbw_metric_label_rejections_total`` so that a caller quietly
    poisoning a metric is visible on a dashboard rather than only in a log file.
    """
    try:
        return check_label(label, value)
    except HighCardinalityLabelError as exc:
        # The rejection counter's own label is a *label name*, always a code constant, so it is
        # passed straight to prometheus_client and cannot recurse back into this function.
        LABEL_REJECTIONS.labels(label=label if label in _LABEL_SPECS else OTHER).inc()
        logger.warning("Refused high-cardinality metric label: %s", exc)
        return OTHER


def _preview(text: str) -> str:
    """Describe a rejected value *without* echoing free text.

    A friendlier message would quote the offending value back - and since the values this guard
    rejects are exactly the questions and SQL statements the module exists to keep out of
    telemetry, that message would land the leak in the application log instead. So a value that
    already looks like an identifier is named (the bounded-cap case, where knowing which profile
    overflowed is the useful part) and anything else is described only by its shape.
    """
    if (
        len(text) <= MAX_LABEL_CHARS
        and not _NOT_AN_IDENTIFIER.search(text)
        and _LABEL_SLUG.match(text)
    ):
        return text
    return f"<{len(text)} chars, not an identifier>"


def reset_bounded_labels() -> None:
    """Forget which bounded values have been admitted. For tests only."""
    with _ADMISSION_LOCK:
        _ADMITTED.clear()


def label_specs() -> tuple[LabelSpec, ...]:
    """All declared label specs, sorted by name (used by tests and documentation)."""
    return tuple(_LABEL_SPECS[name] for name in sorted(_LABEL_SPECS))


# --- Registry and metric declarations --------------------------------------------------------

REGISTRY = CollectorRegistry(auto_describe=True)

MetricKind = Literal["counter", "gauge", "histogram"]


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """The declared shape of one metric: what tests pin and what the dashboards may reference."""

    name: str
    kind: MetricKind
    labels: tuple[str, ...]
    documentation: str


_CATALOGUE: list[MetricSpec] = []

#: Sub-second work: HTTP handling, policy evaluation, retrieval, cache lookups.
_FAST_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
#: Model calls and database queries, which are seconds-scale and occasionally much worse.
_SLOW_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0)
#: Enrollment and evaluation runs, measured in minutes.
_BATCH_BUCKETS = (1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0)


def _declare(name: str, kind: MetricKind, labels: tuple[str, ...], documentation: str) -> None:
    """Record a metric in the catalogue and assert every label has a spec."""
    unknown = [label for label in labels if label not in _LABEL_SPECS]
    if unknown:  # pragma: no cover - a declaration-time programming error
        raise HighCardinalityLabelError(
            f"Metric '{name}' declares labels without a LabelSpec: {', '.join(unknown)}"
        )
    _CATALOGUE.append(MetricSpec(name, kind, labels, documentation))


def _counter(name: str, documentation: str, labels: tuple[str, ...] = ()) -> Counter:
    _declare(name, "counter", labels, documentation)
    return Counter(name, documentation, labels, registry=REGISTRY)


def _gauge(name: str, documentation: str, labels: tuple[str, ...] = ()) -> Gauge:
    _declare(name, "gauge", labels, documentation)
    return Gauge(name, documentation, labels, registry=REGISTRY)


def _histogram(
    name: str,
    documentation: str,
    labels: tuple[str, ...] = (),
    buckets: tuple[float, ...] = _FAST_BUCKETS,
) -> Histogram:
    _declare(name, "histogram", labels, documentation)
    return Histogram(name, documentation, labels, registry=REGISTRY, buckets=buckets)


# HTTP surface. ``outcome`` is a pure function of ``status`` (>=400 is a failure), so carrying
# both does not multiply the series count - it just saves every dashboard the same regex.
HTTP_REQUESTS = _counter(
    "dbw_http_requests_total",
    "HTTP requests handled, by route template, status code and success/failure.",
    ("method", "route", "status", "outcome"),
)
HTTP_DURATION = _histogram(
    "dbw_http_request_duration_seconds",
    "Wall-clock time to handle an HTTP request. p50/p95/p99 via histogram_quantile.",
    ("method", "route"),
)

# Model layer.
MODEL_DURATION = _histogram(
    "dbw_model_call_duration_seconds",
    "Time for one model call, including provider-side queueing.",
    ("provider", "model", "capability"),
    buckets=_SLOW_BUCKETS,
)
MODEL_TOKENS = _counter(
    "dbw_model_tokens_total",
    "Tokens reported by the provider. Absent when a provider does not report usage.",
    ("provider", "model", "kind"),
)
MODEL_CALLS = _counter(
    "dbw_model_calls_total",
    "Model calls attempted, by outcome and error category.",
    ("provider", "model", "outcome", "error_category"),
)
PROVIDER_FALLBACKS = _counter(
    "dbw_provider_fallbacks_total",
    "Times the router abandoned one provider for the next, by normalised failure reason.",
    ("from_provider", "to_provider", "reason"),
)
PROVIDER_CIRCUIT_STATE = _gauge(
    "dbw_provider_circuit_state",
    "Circuit-breaker state per provider: 0 closed, 1 half-open, 2 open.",
    ("provider",),
)

# Retrieval and policy.
RETRIEVAL_DURATION = _histogram(
    "dbw_retrieval_duration_seconds",
    "Time spent in one retrieval stage (bm25, vector, fuse, expand, pack, total).",
    ("stage",),
)
POLICY_DURATION = _histogram(
    "dbw_policy_evaluation_duration_seconds",
    "Time to evaluate the SQL policy engine over one statement (parse plus rules).",
    ("dialect", "level"),
)
POLICY_DECISIONS = _counter(
    "dbw_policy_decisions_total",
    "Policy outcomes by decision and, for denials, the rule that fired.",
    ("decision", "rule", "dialect"),
)

# Execution against the target database.
DATABASE_DURATION = _histogram(
    "dbw_database_query_duration_seconds",
    "Time for one statement against a target database, measured by the execution service.",
    ("dialect", "outcome"),
    buckets=_SLOW_BUCKETS,
)
POOL_CONNECTIONS = _gauge(
    "dbw_connection_pool_connections",
    "Connections held by the execution pools, by dialect and state.",
    ("dialect", "state"),
)

# Agent pipeline.
GRAPH_RUNS = _counter(
    "dbw_graph_runs_total",
    "Query-graph runs that reached a terminal state, by outcome.",
    ("outcome",),
)
GRAPH_CLARIFICATIONS = _counter(
    "dbw_graph_clarifications_total",
    "Runs that paused to ask a clarifying question. Divide by dbw_graph_runs_total for a rate.",
    ("reason",),
)
GRAPH_REPAIRS = _counter(
    "dbw_query_repairs_total",
    "Bounded SQL repair attempts. Divide by dbw_graph_runs_total for a rate.",
    ("reason",),
)

# Background work and caches.
WORKER_QUEUE_DEPTH = _gauge(
    "dbw_worker_queue_depth",
    "Jobs waiting in a background queue, sampled by the worker.",
    ("queue",),
)
ENROLLMENT_DURATION = _histogram(
    "dbw_enrollment_duration_seconds",
    "Time for one data-source enrollment stage (introspect, document, embed, total).",
    ("stage", "outcome"),
    buckets=_BATCH_BUCKETS,
)
EVAL_RUN_DURATION = _histogram(
    "dbw_eval_run_duration_seconds",
    "Wall-clock time for one evaluation suite run.",
    ("suite",),
    buckets=_BATCH_BUCKETS,
)
CACHE_LOOKUPS = _counter(
    "dbw_cache_lookups_total",
    "Cache lookups by cache name and hit/miss. Hit rate is computed in PromQL.",
    ("cache", "result"),
)

# Self-observation: instrumentation that misbehaves should be visible on the same dashboard.
LABEL_REJECTIONS = _counter(
    "dbw_metric_label_rejections_total",
    "Label values refused by the cardinality guard and replaced with 'other'.",
    ("label",),
)
SPAN_ATTRIBUTES_REFUSED = _counter(
    "dbw_span_attributes_refused_total",
    "Span attributes the allowlist dropped, rejected as forbidden, or redacted.",
    ("disposition",),
)
BUILD_INFO = _gauge(
    "dbw_build_info",
    "Always 1. Labels carry the running mode and the SQL policy ruleset version.",
    ("mode", "policy_version"),
)


def metric_catalogue() -> tuple[MetricSpec, ...]:
    """Declared metrics in registration order. Pinned by the test suite."""
    return tuple(_CATALOGUE)


def metric_names() -> frozenset[str]:
    """Names as exposed on ``/metrics`` (counters already carry their ``_total`` suffix)."""
    return frozenset(spec.name for spec in _CATALOGUE)


def _register_process_collectors() -> None:
    """Attach CPU/RSS/GC collectors to our registry.

    They are opt-in in prometheus_client and normally land in the global default registry; we
    want them on ``/metrics`` but under our own registry. ``ProcessCollector`` is a no-op off
    Linux (it reads ``/proc``), which is why this cannot be asserted on in a portable test.
    """
    for collector in (ProcessCollector, PlatformCollector, GCCollector):
        try:
            collector(registry=REGISTRY)
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.debug("Skipped %s: %s", collector.__name__, exc)


_register_process_collectors()


# --- Recording helpers -------------------------------------------------------------------------
# Callers go through these rather than touching the metric objects, so every label value passes
# the cardinality guard exactly once and no magic strings escape into the call sites.


def observe_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    """Record one handled request. ``route`` must be the path *template*, not the raw path.

    Passing ``/schemas/42`` instead of ``/schemas/{id}`` is the classic way to blow up an HTTP
    metric; the bounded ``route`` label caps the damage but the caller should still template it.
    """
    safe_method = safe_label("method", method.upper())
    safe_route = safe_label("route", route)
    outcome = Outcome.FAILURE if status_code >= 400 else Outcome.SUCCESS
    HTTP_REQUESTS.labels(
        method=safe_method,
        route=safe_route,
        status=safe_label("status", str(status_code)),
        outcome=outcome.value,
    ).inc()
    HTTP_DURATION.labels(method=safe_method, route=safe_route).observe(duration_seconds)


def observe_model_call(
    *,
    provider: str,
    model: str,
    capability: str,
    duration_seconds: float,
    outcome: Outcome = Outcome.SUCCESS,
    error_category: ErrorCategory | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """Record one model call. Token counts are optional: not every provider reports usage.

    A successful call is labelled ``error_category="none"`` rather than being split into a second
    metric, so success rate is one PromQL division over a single series family.
    """
    safe_provider = safe_label("provider", provider)
    safe_model = safe_label("model", model)
    MODEL_DURATION.labels(
        provider=safe_provider,
        model=safe_model,
        capability=safe_label("capability", capability),
    ).observe(duration_seconds)
    category = NO_ERROR
    if outcome is Outcome.FAILURE:
        category = (error_category or ErrorCategory.INTERNAL).value
    MODEL_CALLS.labels(
        provider=safe_provider,
        model=safe_model,
        outcome=outcome.value,
        error_category=category,
    ).inc()
    counted = ((TokenKind.PROMPT, prompt_tokens), (TokenKind.COMPLETION, completion_tokens))
    for kind, count in counted:
        if count:
            MODEL_TOKENS.labels(provider=safe_provider, model=safe_model, kind=kind.value).inc(
                count
            )


def record_provider_fallback(*, from_provider: str, to_provider: str, reason: str) -> None:
    """Record the router giving up on one provider for the next."""
    PROVIDER_FALLBACKS.labels(
        from_provider=safe_label("from_provider", from_provider),
        to_provider=safe_label("to_provider", to_provider),
        reason=safe_label("reason", reason),
    ).inc()


def set_circuit_state(*, provider: str, state: CircuitState) -> None:
    """Publish a provider's breaker state as the numeric encoding documented on the gauge."""
    PROVIDER_CIRCUIT_STATE.labels(provider=safe_label("provider", provider)).set(state.code)


def observe_retrieval(*, stage: str, duration_seconds: float) -> None:
    RETRIEVAL_DURATION.labels(stage=safe_label("stage", stage)).observe(duration_seconds)


def observe_policy_evaluation(
    *,
    dialect: str,
    level: str,
    decision: str,
    duration_seconds: float,
    rule: str | None = None,
) -> None:
    """Record one policy evaluation and its decision.

    ``rule`` is the ``rule_id`` that decided a denial - a code-defined identifier, never a
    message. It defaults to ``none`` for an allow, which keeps the series set small.
    """
    safe_dialect = safe_label("dialect", dialect)
    POLICY_DURATION.labels(dialect=safe_dialect, level=safe_label("level", level)).observe(
        duration_seconds
    )
    POLICY_DECISIONS.labels(
        decision=safe_label("decision", decision),
        rule=safe_label("rule", rule) if rule else "none",
        dialect=safe_dialect,
    ).inc()


def observe_database_query(
    *, dialect: str, duration_seconds: float, outcome: Outcome = Outcome.SUCCESS
) -> None:
    DATABASE_DURATION.labels(dialect=safe_label("dialect", dialect), outcome=outcome.value).observe(
        duration_seconds
    )


def set_pool_connections(*, dialect: str, state: PoolState, count: int) -> None:
    POOL_CONNECTIONS.labels(dialect=safe_label("dialect", dialect), state=state.value).set(count)


def record_graph_run(*, outcome: Outcome) -> None:
    GRAPH_RUNS.labels(outcome=outcome.value).inc()


def record_clarification(*, reason: str) -> None:
    GRAPH_CLARIFICATIONS.labels(reason=safe_label("reason", reason)).inc()


def record_repair(*, reason: str) -> None:
    GRAPH_REPAIRS.labels(reason=safe_label("reason", reason)).inc()


def set_worker_queue_depth(*, queue: str, depth: int) -> None:
    WORKER_QUEUE_DEPTH.labels(queue=safe_label("queue", queue)).set(depth)


def observe_enrollment(
    *, stage: str, duration_seconds: float, outcome: Outcome = Outcome.SUCCESS
) -> None:
    ENROLLMENT_DURATION.labels(stage=safe_label("stage", stage), outcome=outcome.value).observe(
        duration_seconds
    )


def observe_eval_run(*, suite: str, duration_seconds: float) -> None:
    EVAL_RUN_DURATION.labels(suite=safe_label("suite", suite)).observe(duration_seconds)


def record_cache_lookup(*, cache: str, result: CacheResult) -> None:
    CACHE_LOOKUPS.labels(cache=safe_label("cache", cache), result=result.value).inc()


def record_span_attributes_refused(
    *, dropped: int = 0, rejected: int = 0, redacted: int = 0
) -> None:
    """Publish what the span attribute allowlist refused. Called by ``spans.span``."""
    tallies = (("dropped", dropped), ("rejected", rejected), ("redacted", redacted))
    for disposition, count in tallies:
        if count:
            SPAN_ATTRIBUTES_REFUSED.labels(disposition=disposition).inc(count)


def set_build_info(*, mode: str, policy_version: str) -> None:
    """Publish the running configuration as a constant-1 gauge (the Prometheus info idiom)."""
    BUILD_INFO.labels(
        mode=safe_label("mode", mode),
        policy_version=safe_label("policy_version", policy_version),
    ).set(1)


def render() -> tuple[bytes, str]:
    """Serialise the registry for a ``/metrics`` endpoint.

    Returns ``(payload, content_type)`` so the caller does not have to know the exposition
    format version. See the module docstring in ``app/observability/__init__.py`` for the exact
    FastAPI route to mount.
    """
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "BUILD_INFO",
    "CACHE_LOOKUPS",
    "DATABASE_DURATION",
    "ENROLLMENT_DURATION",
    "EVAL_RUN_DURATION",
    "GRAPH_CLARIFICATIONS",
    "GRAPH_REPAIRS",
    "GRAPH_RUNS",
    "HTTP_DURATION",
    "HTTP_REQUESTS",
    "LABEL_REJECTIONS",
    "MODEL_CALLS",
    "MODEL_DURATION",
    "MODEL_TOKENS",
    "NO_ERROR",
    "OTHER",
    "POLICY_DECISIONS",
    "POLICY_DURATION",
    "POOL_CONNECTIONS",
    "PROVIDER_CIRCUIT_STATE",
    "PROVIDER_FALLBACKS",
    "REGISTRY",
    "RETRIEVAL_DURATION",
    "SPAN_ATTRIBUTES_REFUSED",
    "WORKER_QUEUE_DEPTH",
    "CacheResult",
    "CircuitState",
    "HighCardinalityLabelError",
    "LabelSpec",
    "MetricSpec",
    "Outcome",
    "PoolState",
    "TokenKind",
    "check_label",
    "label_specs",
    "metric_catalogue",
    "metric_names",
    "observe_database_query",
    "observe_enrollment",
    "observe_eval_run",
    "observe_http_request",
    "observe_model_call",
    "observe_policy_evaluation",
    "observe_retrieval",
    "record_cache_lookup",
    "record_clarification",
    "record_graph_run",
    "record_provider_fallback",
    "record_repair",
    "record_span_attributes_refused",
    "render",
    "reset_bounded_labels",
    "safe_label",
    "set_build_info",
    "set_circuit_state",
    "set_pool_connections",
    "set_worker_queue_depth",
]
