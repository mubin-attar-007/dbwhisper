# ADR 0017 — Observability is off by default, and a closed allowlist decides what a signal may carry

**Status:** Accepted — 2026-08-24, Phase 11.

## Context

DBWhisper's telemetry describes work performed on somebody else's database. The attributes that
would be most useful on a span — the question, the generated SQL, the rows that came back, the DSN
that was dialled — are exactly the ones that would turn a trace backend into an uncontrolled second
copy of the customer's data, usually with weaker access control than the database it came from.

Separately, instrumentation is a common cause of outages it was installed to diagnose: an exporter
that blocks, a collector that is not running, an SDK that fails at import.

## Decision

**Off unless asked.** `app/observability/otel.py`: with `OTEL_ENABLED=false` (the default) no
provider is created, no exporter thread starts, and `get_tracer()` returns `None`; the span helper
in `app/observability/spans.py` degrades to a no-op, so instrumented code paths are identical
whether or not anyone is collecting. Every step of setup is wrapped — a malformed endpoint, a
missing exporter package or an unreachable collector leaves the application serving requests with
tracing disabled and one warning in the log. Exports run on `BatchSpanProcessor`'s background
thread with a bounded timeout.

**An attribute is dropped unless its key is on a closed allowlist.**
`app/observability/attributes.py` inverts the usual "redact anything that looks secret". A denylist
fails the moment somebody adds an attribute nobody thought about; an allowlist fails safe by
construction. The denylist is kept as a *smoke alarm*: an attempt to attach `password` is dropped by the
allowlist before the denylist sees it, but it is counted and logged, so the mistake surfaces
instead of passing silently. Order is
load-bearing — allowlist first, because `dbw.sql_fingerprint` contains the substring `sql` and would
otherwise trip the denylist.

**Metric labels come from sets bounded by code, never by user input.**
`app/observability/metrics.py` allows two label kinds: *closed* (value must be in a literal set) and
*bounded* (slug-shaped, first `max_values` distinct values admitted, everything after collapses to
`other`, and the collapse is itself counted). No SQL, question text, user id, table name or error
message may ever be a label value.

## Consequences

**What it buys.** A missing exporter package, a malformed endpoint or a collector that is not
running leaves the application serving requests with one warning in the log, and a trace backend
does not become a shadow copy of customer data. Prometheus cardinality is bounded by the code rather
than by whatever a user types. `tests/test_observability.py` exercises the allowlist end to end
against a real exporter, and the provisioned Grafana dashboards are asserted against the declared
metric names, so a renamed metric fails a test rather than producing an empty panel.

**What it costs.**

* **Debugging from traces alone is harder.** The interesting values are deliberately absent; an
  engineer needs the run record and the access to read it. That is the intended trade and it will be
  felt during the first production incident.
* **Adding an attribute means editing the allowlist.** Friction is the feature; it is still friction,
  and it is the kind of friction that tempts someone to widen the list rather than think.
* **Bounded labels lose granularity.** A deployment with more models than `max_values` sees the
  overflow as `other`. The metric degrades rather than exploding, but the data is gone.
* **Off by default means most deployments run without traces**, so the instrumentation is exercised
  by tests and by operators who opt in — not continuously by us.
* **The default is a choice about who bears the risk**: we default to less visibility for the
  operator in exchange for no accidental data export. A vendor selling observability would choose
  the opposite.

**Enforcement.** The import contract `Observability is a leaf` forbids `app.observability` from
importing `app.api`, `app.graph`, `app.execution`, `app.llm`, `app.jobs`, `app.sqlpolicy` or
`app.retrieval` — instrumentation that imports what it instruments causes cycles and tempts someone
to put a result row on a span.
