"""Tests for the observability package.

These assert the two properties the package exists to guarantee, and they assert them against
real objects rather than mocks:

* **Nothing forbidden reaches a span.** The end-to-end cases run a real ``TracerProvider`` with an
  in-memory exporter and inspect the exported span, so they would catch a leak introduced anywhere
  between the call site and the exporter - not just a change to the scrubbing function.
* **Nothing unbounded reaches a metric label.** The cardinality guard is exercised with the exact
  values that would cause the damage (raw SQL, a question, a DSN, an id flood).

Two further tests exist to stop *documentation* drifting from code: the metric catalogue is pinned
name-by-name, and every PromQL expression in the provisioned Grafana dashboards is checked against
the declared metrics and labels. A renamed metric fails the suite instead of silently emptying a
panel.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client.parser import text_string_to_metric_families

from app.core.config import Settings
from app.observability import attributes as attrs
from app.observability import metrics as m
from app.observability import otel, spans

REPO_ROOT = Path(__file__).resolve().parents[1]
OPS = REPO_ROOT / "ops"
DASHBOARDS = OPS / "grafana" / "dashboards"

#: A value that must never survive into any signal, used as a canary throughout.
CANARY = "s3cr3t-canary-value"


@pytest.fixture(autouse=True)
def _isolate_observability_state():
    """Bounded-label admissions and the tracer provider are process-global; reset around tests."""
    m.reset_bounded_labels()
    otel.shutdown_tracing()
    yield
    otel.shutdown_tracing()
    m.reset_bounded_labels()


@pytest.fixture
def exported_spans():
    """Tracing enabled, exporting to memory. Yields a callable returning the finished spans."""
    exporter = InMemorySpanExporter()
    settings = Settings(otel_enabled=True, otel_service_name="dbwhisper-test")
    status = otel.setup_tracing(settings=settings, span_processor=SimpleSpanProcessor(exporter))
    assert status.enabled and status.exporter == "custom"
    yield exporter.get_finished_spans


# --------------------------------------------------------------------------------------------
# Disabled by default
# --------------------------------------------------------------------------------------------


def test_setup_is_a_noop_when_otel_is_disabled() -> None:
    status = otel.setup_tracing(settings=Settings(otel_enabled=False))
    assert status == otel.DISABLED
    assert status.enabled is False
    assert otel.tracing_enabled() is False
    assert otel.get_tracer() is None


def test_span_works_and_records_nothing_when_tracing_is_disabled() -> None:
    otel.setup_tracing(settings=Settings(otel_enabled=False))
    with spans.span("execute", **{attrs.SpanAttr.DIALECT.value: "postgres"}) as handle:
        assert handle.otel_span is None
        assert handle.recording is False
        # The handle stays usable: call sites carry no `if tracing_enabled()` branches.
        handle.set(**{attrs.SpanAttr.ROW_COUNT.value: 3})
        handle.record_error(attrs.ErrorCategory.TIMEOUT)
    assert spans.current_trace_id() is None


def test_disabled_setup_does_not_leave_a_provider_behind() -> None:
    otel.setup_tracing(settings=Settings(otel_enabled=False))
    assert otel.status().instrumented_fastapi is False
    assert otel.get_tracer("anything") is None


def test_setup_never_raises_on_an_unusable_endpoint() -> None:
    """A collector that does not exist must not stop the application from starting."""
    settings = Settings(otel_enabled=True, otel_exporter_otlp_endpoint="http://127.0.0.1:1/")
    status = otel.setup_tracing(settings=settings)
    # Construction succeeds (the exporter is lazy and batched); what matters is no exception and
    # that the app is told what happened.
    assert status.enabled is True
    assert status.exporter == "otlp_http"
    with spans.span("execute"):
        pass  # Would raise here if an unreachable collector propagated its failure.


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://collector:4318", "http://collector:4318/v1/traces"),
        ("http://collector:4318/", "http://collector:4318/v1/traces"),
        ("http://collector:4318/v1/traces", "http://collector:4318/v1/traces"),
    ],
)
def test_otlp_endpoint_accepts_base_or_full_url(configured: str, expected: str) -> None:
    assert otel._traces_endpoint(configured) == expected


# --------------------------------------------------------------------------------------------
# The attribute allowlist
# --------------------------------------------------------------------------------------------


def test_allowlist_keeps_allowed_keys_and_drops_unknown_ones() -> None:
    result = attrs.scrub_attributes(
        {
            attrs.SpanAttr.PROVIDER.value: "ollama",
            attrs.SpanAttr.ROW_COUNT.value: 42,
            attrs.SpanAttr.CACHE_HIT.value: True,
            "dbw.something_new": "nope",
        }
    )
    assert result.attributes == {
        "dbw.provider": "ollama",
        "dbw.row_count": 42,
        "dbw.cache_hit": True,
    }
    assert result.dropped == ("dbw.something_new",)
    # bool must survive as bool, not as 1 - `isinstance(True, int)` is the trap here.
    assert result.attributes["dbw.cache_hit"] is True


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "api_key",
        "connection_string",
        "postgres_dsn",
        "authorization",
        "session_token",
        "user_email",
        "question",
        "prompt",
        "sql",
        "rows",
        "result_sample",
    ],
)
def test_forbidden_keys_are_rejected_not_merely_dropped(key: str) -> None:
    result = attrs.scrub_attributes({key: CANARY})
    assert result.attributes == {}
    assert key in result.rejected, f"{key} should be reported as forbidden, not silently dropped"


def test_allowlist_is_checked_before_the_denylist() -> None:
    """`dbw.sql_fingerprint` contains 'sql'; ordering is what keeps it usable."""
    result = attrs.scrub_attributes({attrs.SpanAttr.SQL_FINGERPRINT.value: "a1b2c3d4"})
    assert result.attributes == {"dbw.sql_fingerprint": "a1b2c3d4"}
    assert result.rejected == ()


def test_non_scalar_values_are_refused() -> None:
    """A list is the shape result rows arrive in; str() would smuggle them through."""
    result = attrs.scrub_attributes(
        {
            attrs.SpanAttr.ROW_COUNT.value: [(1, CANARY), (2, CANARY)],
            attrs.SpanAttr.STEP.value: {"secret": CANARY},
        }
    )
    assert result.attributes == {}
    assert set(result.dropped) == {"dbw.row_count", "dbw.step"}


def test_credential_shaped_values_are_redacted_under_an_allowed_key() -> None:
    result = attrs.scrub_attributes(
        {
            attrs.SpanAttr.PROVIDER.value: f"postgresql://user:{CANARY}@db.internal:5432/app",
            attrs.SpanAttr.MODEL.value: f"api_key={CANARY}",
        }
    )
    assert result.attributes == {
        "dbw.provider": attrs.REDACTED,
        "dbw.model": attrs.REDACTED,
    }
    assert set(result.redacted) == {"dbw.provider", "dbw.model"}


def test_long_strings_are_truncated() -> None:
    result = attrs.scrub_attributes({attrs.SpanAttr.STEP.value: "x" * 500})
    assert len(result.attributes["dbw.step"]) == attrs.MAX_STRING_CHARS


def test_error_category_comes_from_the_exception_type_only() -> None:
    assert attrs.classify_exception(TimeoutError(CANARY)) is attrs.ErrorCategory.TIMEOUT
    assert attrs.classify_exception(ConnectionError(CANARY)) is attrs.ErrorCategory.CONNECTION
    assert attrs.classify_exception(PermissionError(CANARY)) is attrs.ErrorCategory.PERMISSION
    assert attrs.classify_exception(ValueError(CANARY)) is attrs.ErrorCategory.VALIDATION
    assert attrs.classify_exception(RuntimeError(CANARY)) is attrs.ErrorCategory.INTERNAL


def test_org_id_is_hashed_and_salt_changes_the_digest() -> None:
    assert attrs.hash_org_id(None) is None
    assert attrs.hash_org_id("  ") is None
    first = attrs.hash_org_id(17, salt="salt-a")
    assert first is not None and len(first) == 16 and "17" not in first
    assert attrs.hash_org_id(17, salt="salt-a") == first
    assert attrs.hash_org_id(17, salt="salt-b") != first


# --------------------------------------------------------------------------------------------
# End to end: what actually lands on an exported span
# --------------------------------------------------------------------------------------------


def _all_attribute_text(finished: list[Any]) -> str:
    parts: list[str] = []
    for span_data in finished:
        parts.append(span_data.name)
        for key, value in (span_data.attributes or {}).items():
            parts.append(f"{key}={value}")
        for event in span_data.events:
            parts.append(event.name)
            for key, value in (event.attributes or {}).items():
                parts.append(f"{key}={value}")
        if span_data.status.description:
            parts.append(span_data.status.description)
    return "\n".join(parts)


def test_forbidden_attributes_never_reach_an_exported_span(exported_spans) -> None:
    with spans.span(
        "execute",
        **{
            attrs.SpanAttr.DIALECT.value: "postgres",
            attrs.SpanAttr.SQL_FINGERPRINT.value: "f00dcafe",
            "password": CANARY,
            "connection_string": f"postgresql://u:{CANARY}@h/db",
            "question": f"how many {CANARY} are there",
            "rows": [(1, CANARY)],
        },
    ) as handle:
        handle.set(**{"api_key": CANARY, attrs.SpanAttr.ROW_COUNT.value: 7})

    finished = exported_spans()
    assert len(finished) == 1
    exported = finished[0]
    assert exported.attributes["dbw.dialect"] == "postgres"
    assert exported.attributes["dbw.sql_fingerprint"] == "f00dcafe"
    assert exported.attributes["dbw.row_count"] == 7
    assert set(exported.attributes) <= attrs.ALLOWED_KEYS
    assert CANARY not in _all_attribute_text(finished)


def test_exception_reaches_the_span_as_a_category_without_its_message(exported_spans) -> None:
    from opentelemetry.trace import StatusCode

    with pytest.raises(TimeoutError), spans.span("execute"):
        raise TimeoutError(f"connection to postgresql://u:{CANARY}@h/db timed out")

    finished = exported_spans()
    exported = finished[0]
    assert exported.status.status_code is StatusCode.ERROR
    assert exported.attributes["dbw.error_category"] == attrs.ErrorCategory.TIMEOUT.value
    # record_exception=False: no exception event, so no message and no stack trace are exported.
    assert exported.events == ()
    assert CANARY not in _all_attribute_text(finished)


def test_span_records_latency_and_a_trace_id_is_available(exported_spans) -> None:
    with spans.span("execute") as handle:
        assert handle.recording is True
        trace_id = spans.current_trace_id()
    assert trace_id is not None and len(trace_id) == 32
    exported = exported_spans()[0]
    assert exported.attributes["dbw.latency_ms"] >= 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("execute", "execute"),
        ("graph.retrieve", "graph.retrieve"),
        ("Execute The Query", "execute_the_query"),
        ("SELECT * FROM customers WHERE name = 'x'", "select_from_customers_where_name_x"),
        ("", "unnamed"),
        ("123", "unnamed_123"),
    ],
)
def test_span_names_are_normalised_to_slugs(raw: str, expected: str) -> None:
    assert spans.normalize_span_name(raw) == expected


def test_a_question_used_as_a_span_name_cannot_become_free_text(exported_spans) -> None:
    with spans.span(f"how many {CANARY} orders were placed?"):
        pass
    exported = exported_spans()[0]
    assert " " not in exported.name
    assert len(exported.name) <= 64


def test_setup_tracing_instruments_a_fastapi_app_and_scrubs_the_query_string() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/echo")
    async def echo() -> dict[str, str]:
        return {"ok": "yes"}

    exporter = InMemorySpanExporter()
    status = otel.setup_tracing(
        app,
        settings=Settings(otel_enabled=True),
        span_processor=SimpleSpanProcessor(exporter),
    )
    assert status.instrumented_fastapi is True

    with TestClient(app) as client:
        assert client.get(f"/echo?q={CANARY}").status_code == 200

    finished = exporter.get_finished_spans()
    assert finished, "FastAPI instrumentation produced no server span"
    assert CANARY not in _all_attribute_text(list(finished))


# --------------------------------------------------------------------------------------------
# Metrics: stable shape, valid exposition, enforced cardinality
# --------------------------------------------------------------------------------------------

EXPECTED_CATALOGUE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("dbw_http_requests_total", "counter", ("method", "route", "status", "outcome")),
    ("dbw_http_request_duration_seconds", "histogram", ("method", "route")),
    ("dbw_model_call_duration_seconds", "histogram", ("provider", "model", "capability")),
    ("dbw_model_tokens_total", "counter", ("provider", "model", "kind")),
    ("dbw_model_calls_total", "counter", ("provider", "model", "outcome", "error_category")),
    ("dbw_provider_fallbacks_total", "counter", ("from_provider", "to_provider", "reason")),
    ("dbw_provider_circuit_state", "gauge", ("provider",)),
    ("dbw_retrieval_duration_seconds", "histogram", ("stage",)),
    ("dbw_policy_evaluation_duration_seconds", "histogram", ("dialect", "level")),
    ("dbw_policy_decisions_total", "counter", ("decision", "rule", "dialect")),
    ("dbw_database_query_duration_seconds", "histogram", ("dialect", "outcome")),
    ("dbw_connection_pool_connections", "gauge", ("dialect", "state")),
    ("dbw_graph_runs_total", "counter", ("outcome",)),
    ("dbw_graph_clarifications_total", "counter", ("reason",)),
    ("dbw_query_repairs_total", "counter", ("reason",)),
    ("dbw_worker_queue_depth", "gauge", ("queue",)),
    ("dbw_enrollment_duration_seconds", "histogram", ("stage", "outcome")),
    ("dbw_eval_run_duration_seconds", "histogram", ("suite",)),
    ("dbw_cache_lookups_total", "counter", ("cache", "result")),
    ("dbw_metric_label_rejections_total", "counter", ("label",)),
    ("dbw_span_attributes_refused_total", "counter", ("disposition",)),
    ("dbw_build_info", "gauge", ("mode", "policy_version")),
)


def test_metric_catalogue_is_stable() -> None:
    """Renaming a metric or changing its labels breaks dashboards and alerts. Pin both."""
    actual = tuple((s.name, s.kind, s.labels) for s in m.metric_catalogue())
    assert actual == EXPECTED_CATALOGUE


def test_every_declared_metric_has_documentation() -> None:
    for spec in m.metric_catalogue():
        assert spec.documentation.strip(), f"{spec.name} has no help text"
        assert spec.documentation.strip().endswith("."), f"{spec.name} help is not a sentence"


def test_each_required_signal_from_the_brief_has_a_metric() -> None:
    """Coverage check written as the mapping a reviewer would otherwise have to reconstruct."""
    required = {
        "request count": "dbw_http_requests_total",
        "success/failure": "dbw_http_requests_total",
        "status-code distribution": "dbw_http_requests_total",
        "request latency quantiles": "dbw_http_request_duration_seconds",
        "model latency": "dbw_model_call_duration_seconds",
        "retrieval latency": "dbw_retrieval_duration_seconds",
        "validator latency": "dbw_policy_evaluation_duration_seconds",
        "database latency": "dbw_database_query_duration_seconds",
        "token usage": "dbw_model_tokens_total",
        "provider fallbacks": "dbw_provider_fallbacks_total",
        "provider circuit state": "dbw_provider_circuit_state",
        "query-policy blocks": "dbw_policy_decisions_total",
        "clarification rate": "dbw_graph_clarifications_total",
        "query-repair rate": "dbw_query_repairs_total",
        "connection-pool use": "dbw_connection_pool_connections",
        "worker queue depth": "dbw_worker_queue_depth",
        "enrollment duration": "dbw_enrollment_duration_seconds",
        "eval run duration": "dbw_eval_run_duration_seconds",
        "cache hit rate": "dbw_cache_lookups_total",
    }
    declared = m.metric_names()
    missing = {signal: name for signal, name in required.items() if name not in declared}
    assert not missing, f"no metric declared for: {missing}"


def test_render_returns_parseable_prometheus_text() -> None:
    m.observe_http_request(method="POST", route="/query", status_code=200, duration_seconds=0.12)
    payload, content_type = m.render()
    assert content_type.startswith("text/plain")
    families = list(text_string_to_metric_families(payload.decode("utf-8")))
    by_name = {family.name: family for family in families}
    # prometheus_client strips the `_total` suffix from the family name it reports.
    assert "dbw_http_requests" in by_name
    assert "dbw_http_request_duration_seconds" in by_name
    value = m.REGISTRY.get_sample_value(
        "dbw_http_requests_total",
        {"method": "POST", "route": "/query", "status": "200", "outcome": "success"},
    )
    assert value is not None and value >= 1.0


def test_http_outcome_is_derived_from_the_status_code() -> None:
    m.observe_http_request(
        method="GET", route="/schemas/{id}", status_code=503, duration_seconds=0.4
    )
    failed = m.REGISTRY.get_sample_value(
        "dbw_http_requests_total",
        {"method": "GET", "route": "/schemas/{id}", "status": "503", "outcome": "failure"},
    )
    assert failed is not None and failed >= 1.0


def test_histogram_buckets_are_present_so_quantiles_can_be_computed() -> None:
    m.observe_model_call(
        provider="fake", model="fake-small", capability="sql", duration_seconds=0.3
    )
    bucket = m.REGISTRY.get_sample_value(
        "dbw_model_call_duration_seconds_bucket",
        {"provider": "fake", "model": "fake-small", "capability": "sql", "le": "0.5"},
    )
    assert bucket is not None and bucket >= 1.0


def test_model_tokens_are_only_recorded_when_reported() -> None:
    labels = {"provider": "fake", "model": "no-usage", "kind": "prompt"}
    m.observe_model_call(provider="fake", model="no-usage", capability="sql", duration_seconds=0.1)
    assert m.REGISTRY.get_sample_value("dbw_model_tokens_total", labels) is None
    m.observe_model_call(
        provider="fake",
        model="no-usage",
        capability="sql",
        duration_seconds=0.1,
        prompt_tokens=120,
    )
    assert m.REGISTRY.get_sample_value("dbw_model_tokens_total", labels) == 120.0


def test_circuit_state_gauge_uses_the_documented_encoding() -> None:
    m.set_circuit_state(provider="ollama", state=m.CircuitState.OPEN)
    assert m.REGISTRY.get_sample_value("dbw_provider_circuit_state", {"provider": "ollama"}) == 2.0
    m.set_circuit_state(provider="ollama", state=m.CircuitState.CLOSED)
    assert m.REGISTRY.get_sample_value("dbw_provider_circuit_state", {"provider": "ollama"}) == 0.0


# --- the cardinality guard ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        "SELECT * FROM customers WHERE email = 'a@b.c'",
        "how many orders were placed last quarter?",
        "postgresql://user:pw@host:5432/db",
        "x" * 200,
        "has spaces",
    ],
)
def test_high_cardinality_guard_rejects_free_text_as_a_label(bad_value: str) -> None:
    with pytest.raises(m.HighCardinalityLabelError):
        m.check_label("route", bad_value)


def test_closed_label_rejects_a_value_outside_its_set() -> None:
    assert m.check_label("outcome", "success") == "success"
    with pytest.raises(m.HighCardinalityLabelError):
        m.check_label("outcome", "kind-of-worked")


def test_unknown_label_names_are_rejected_outright() -> None:
    with pytest.raises(m.HighCardinalityLabelError):
        m.check_label("user_id", "42")


def test_bounded_label_admits_up_to_its_cap_then_refuses() -> None:
    spec = next(s for s in m.label_specs() if s.name == "provider")
    for index in range(spec.max_values):
        assert m.check_label("provider", f"provider-{index}") == f"provider-{index}"
    # Already-admitted values keep working; only genuinely new ones are refused.
    assert m.check_label("provider", "provider-0") == "provider-0"
    with pytest.raises(m.HighCardinalityLabelError):
        m.check_label("provider", "one-too-many")


def test_safe_label_degrades_instead_of_raising_and_counts_the_refusal() -> None:
    before = (
        m.REGISTRY.get_sample_value("dbw_metric_label_rejections_total", {"label": "route"}) or 0.0
    )
    assert m.safe_label("route", "SELECT 1 FROM t") == m.OTHER
    after = m.REGISTRY.get_sample_value("dbw_metric_label_rejections_total", {"label": "route"})
    assert after == before + 1.0


def test_recording_helpers_never_raise_on_a_poisoned_label() -> None:
    """The runtime path must survive a caller that passes the question by mistake."""
    m.observe_retrieval(stage="what were sales last year?", duration_seconds=0.05)
    collapsed = m.REGISTRY.get_sample_value(
        "dbw_retrieval_duration_seconds_count", {"stage": m.OTHER}
    )
    assert collapsed is not None and collapsed >= 1.0


def test_every_declared_label_has_a_spec() -> None:
    known = {spec.name for spec in m.label_specs()}
    for metric in m.metric_catalogue():
        for label in metric.labels:
            assert label in known, f"{metric.name} uses undeclared label {label}"


# --- mirrored vocabularies ---------------------------------------------------------------------
# metrics.py deliberately re-declares a few enums instead of importing the domain packages, so
# that scraping /metrics does not drag in sqlglot or the model layer. These tests turn that
# duplication into a checked mirror.


def test_circuit_state_mirrors_the_router_breaker_state() -> None:
    from app.llm.router import BreakerState

    assert {s.value for s in m.CircuitState} == {s.value for s in BreakerState}


def test_policy_vocabularies_mirror_the_sql_policy_engine() -> None:
    from app.sqlpolicy import Decision, Dialect, PolicyLevel

    assert {d.value for d in Dialect} == m._DIALECTS
    assert {level.value for level in PolicyLevel} == m._POLICY_LEVELS
    assert {d.value for d in Decision} == m._POLICY_DECISIONS


def test_mode_vocabulary_mirrors_the_app_modes() -> None:
    from app.platform.modes import AppMode

    assert {mode.value for mode in AppMode} == m._APP_MODES


def test_build_info_accepts_the_real_mode_and_policy_version() -> None:
    from app.sqlpolicy import POLICY_VERSION

    settings = Settings()
    m.set_build_info(mode=settings.mode.value, policy_version=POLICY_VERSION)
    value = m.REGISTRY.get_sample_value(
        "dbw_build_info",
        {"mode": settings.mode.value, "policy_version": POLICY_VERSION},
    )
    assert value == 1.0, "the real POLICY_VERSION must be an acceptable label value"


def test_span_refusals_are_published_as_a_metric() -> None:
    before = (
        m.REGISTRY.get_sample_value(
            "dbw_span_attributes_refused_total", {"disposition": "rejected"}
        )
        or 0.0
    )
    with spans.span("execute", **{"password": CANARY}):
        pass
    after = m.REGISTRY.get_sample_value(
        "dbw_span_attributes_refused_total", {"disposition": "rejected"}
    )
    assert after == before + 1.0


# --------------------------------------------------------------------------------------------
# ops/: the operator-side files must be valid and must agree with the code
# --------------------------------------------------------------------------------------------

_METRIC_REF = re.compile(r"\bdbw_[a-z0-9_]+\b")
_BY_CLAUSE = re.compile(r"\bby\s*\(([^)]*)\)")
_MATCHER_BLOCK = re.compile(r"\{([^}]*)\}")
_MATCHER_LABEL = re.compile(r"([a-z_][a-z0-9_]*)\s*(?:=~|!~|!=|=)")

#: Labels that exist in PromQL or come from the scrape config rather than from our declarations.
_EXTERNAL_LABELS = {"le", "job", "instance", "component", "deployment"}


def _dashboard_files() -> list[Path]:
    return sorted(DASHBOARDS.glob("*.json"))


def _expressions(dashboard: dict[str, Any]) -> list[str]:
    return [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    ]


def _base_metric_name(reference: str) -> str:
    for suffix in ("_bucket", "_count", "_sum"):
        if reference.endswith(suffix):
            candidate = reference[: -len(suffix)]
            if candidate in m.metric_names():
                return candidate
    return reference


def test_ops_files_exist() -> None:
    assert (OPS / "otel-collector-config.yaml").is_file()
    assert (OPS / "prometheus.yml").is_file()
    assert (OPS / "grafana" / "provisioning" / "datasources" / "datasources.yaml").is_file()
    assert (OPS / "grafana" / "provisioning" / "dashboards" / "dashboards.yaml").is_file()
    assert len(_dashboard_files()) >= 2


def test_collector_config_is_valid_and_scrubs_the_dangerous_attributes() -> None:
    config = yaml.safe_load((OPS / "otel-collector-config.yaml").read_text(encoding="utf-8"))
    pipeline = config["service"]["pipelines"]["traces"]
    assert "attributes/scrub" in pipeline["processors"], "scrubbing must be in the pipeline"
    deleted = {
        action["key"]
        for action in config["processors"]["attributes/scrub"]["actions"]
        if action["action"] == "delete"
    }
    # Third-party instrumentation is not bound by our allowlist; these are the keys that would
    # carry SQL, credentials or an exception message if any of it were ever added.
    for key in (
        "db.statement",
        "db.connection_string",
        "url.query",
        "exception.message",
        "exception.stacktrace",
    ):
        assert key in deleted, f"collector must delete {key}"
    for exporter in pipeline["exporters"]:
        assert exporter in config["exporters"]


def test_prometheus_config_scrapes_the_metrics_endpoint() -> None:
    config = yaml.safe_load((OPS / "prometheus.yml").read_text(encoding="utf-8"))
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    assert jobs["dbwhisper-api"]["metrics_path"] == "/metrics"
    assert config["global"]["scrape_interval"].endswith("s")


def test_grafana_dashboards_are_valid_and_uniquely_identified() -> None:
    uids: set[str] = set()
    for path in _dashboard_files():
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        assert dashboard["title"], f"{path.name} has no title"
        assert dashboard["panels"], f"{path.name} has no panels"
        assert dashboard["uid"] not in uids, f"duplicate dashboard uid in {path.name}"
        uids.add(dashboard["uid"])
        for panel in dashboard["panels"]:
            assert panel["title"]
            assert set(panel["gridPos"]) == {"h", "w", "x", "y"}
    assert {"dbw-api-health", "dbw-agent-pipeline"} <= uids


def test_dashboards_reference_the_provisioned_datasource() -> None:
    provisioned = yaml.safe_load(
        (OPS / "grafana" / "provisioning" / "datasources" / "datasources.yaml").read_text(
            encoding="utf-8"
        )
    )
    known_uids = {source["uid"] for source in provisioned["datasources"]}
    for path in _dashboard_files():
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            uid = panel["datasource"]["uid"]
            assert uid in known_uids, f"{path.name}/{panel['title']} points at unknown {uid}"


def test_every_dashboard_expression_uses_a_declared_metric() -> None:
    """A renamed metric must fail the suite, not silently empty a panel."""
    declared = m.metric_names()
    for path in _dashboard_files():
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for expr in _expressions(dashboard):
            referenced = {_base_metric_name(ref) for ref in _METRIC_REF.findall(expr)}
            assert referenced, f"{path.name}: expression references no metric: {expr}"
            unknown = referenced - declared
            assert not unknown, f"{path.name}: undeclared metric(s) {unknown} in: {expr}"


def test_every_dashboard_expression_uses_a_declared_label() -> None:
    declared = {spec.name for spec in m.label_specs()} | _EXTERNAL_LABELS
    for path in _dashboard_files():
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for expr in _expressions(dashboard):
            used: set[str] = set()
            for clause in _BY_CLAUSE.findall(expr):
                used.update(part.strip() for part in clause.split(",") if part.strip())
            for block in _MATCHER_BLOCK.findall(expr):
                used.update(_MATCHER_LABEL.findall(block))
            unknown = used - declared
            assert not unknown, f"{path.name}: undeclared label(s) {unknown} in: {expr}"


# --------------------------------------------------------------------------------------------
# HTTP middleware: the route label must be a template, never a raw path
# --------------------------------------------------------------------------------------------


def _metrics_app():
    from fastapi import FastAPI

    from app.observability.middleware import MetricsMiddleware

    app = FastAPI()
    app.add_middleware(MetricsMiddleware)

    @app.get("/schemas/{database_id}")
    async def read_schema(database_id: str) -> dict[str, str]:
        return {"id": database_id}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("failure inside the application")

    @app.get("/metrics")
    async def metrics_endpoint() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_middleware_labels_requests_with_the_route_template_not_the_raw_path() -> None:
    from fastapi.testclient import TestClient

    labels = {
        "method": "GET",
        "route": "/schemas/{database_id}",
        "status": "200",
        "outcome": "success",
    }
    before = m.REGISTRY.get_sample_value("dbw_http_requests_total", labels) or 0.0

    with TestClient(_metrics_app()) as client:
        for database_id in ("9f3c1a", "2b7e00", "c0ffee"):
            assert client.get(f"/schemas/{database_id}").status_code == 200

    after = m.REGISTRY.get_sample_value("dbw_http_requests_total", labels)
    assert after == before + 3.0, "three ids must share one series, not create three"
    # The raw paths must not have produced series of their own.
    for database_id in ("9f3c1a", "2b7e00", "c0ffee"):
        raw = dict(labels, route=f"/schemas/{database_id}")
        assert m.REGISTRY.get_sample_value("dbw_http_requests_total", raw) is None


def test_middleware_records_unmatched_paths_as_a_single_series() -> None:
    from fastapi.testclient import TestClient

    from app.observability.middleware import UNMATCHED_ROUTE

    labels = {"method": "GET", "route": UNMATCHED_ROUTE, "status": "404", "outcome": "failure"}
    before = m.REGISTRY.get_sample_value("dbw_http_requests_total", labels) or 0.0

    with TestClient(_metrics_app()) as client:
        assert client.get("/nope/one").status_code == 404
        assert client.get("/nope/two").status_code == 404

    after = m.REGISTRY.get_sample_value("dbw_http_requests_total", labels)
    assert after == before + 2.0


def test_middleware_records_a_500_when_the_application_raises() -> None:
    from fastapi.testclient import TestClient

    labels = {"method": "GET", "route": "/boom", "status": "500", "outcome": "failure"}
    before = m.REGISTRY.get_sample_value("dbw_http_requests_total", labels) or 0.0

    client = TestClient(_metrics_app(), raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500

    after = m.REGISTRY.get_sample_value("dbw_http_requests_total", labels)
    assert after == before + 1.0


def test_middleware_skips_the_scrape_endpoint() -> None:
    from fastapi.testclient import TestClient

    labels = {"method": "GET", "route": "/metrics", "status": "200", "outcome": "success"}
    with TestClient(_metrics_app()) as client:
        assert client.get("/metrics").status_code == 200
    assert m.REGISTRY.get_sample_value("dbw_http_requests_total", labels) is None


def test_rejection_messages_do_not_echo_the_rejected_value(caplog) -> None:
    """The guard must not leak into the log the very text it refused to put in a metric."""
    question = "how many s3cr3t-canary-value orders were placed?"
    with pytest.raises(m.HighCardinalityLabelError) as raised:
        m.check_label("route", question)
    assert CANARY not in str(raised.value)
    assert "not an identifier" in str(raised.value)

    with caplog.at_level("WARNING", logger="app.observability.metrics"):
        assert m.safe_label("route", question) == m.OTHER
    assert CANARY not in caplog.text


def test_rejection_message_names_an_identifier_that_merely_overflowed_the_cap() -> None:
    """Knowing *which* profile overflowed is useful and safe: it is already an identifier."""
    spec = next(s for s in m.label_specs() if s.name == "model")
    for index in range(spec.max_values):
        m.check_label("model", f"model-{index}")
    with pytest.raises(m.HighCardinalityLabelError) as raised:
        m.check_label("model", "local-bge-small")
    assert "local-bge-small" in str(raised.value)
