"""The audit record must be complete enough to reconstruct an incident and empty of everything else.

The load-bearing tests here are the negative ones: an audit log that quietly accumulates DSNs or
result rows turns a security control into a second copy of the data it protects.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from app.platform.audit import (
    ActorKind,
    AuditAction,
    AuditActor,
    AuditEvent,
    AuditOutcome,
    InMemoryAuditSink,
    LoggingAuditSink,
    audit_to,
    composite_sink,
    get_audit_sink,
    record,
    sanitize_detail,
    set_audit_sink,
)

DSN = "postgresql://analyst:hunter2@db.internal:5432/warehouse"


def event(**overrides) -> AuditEvent:
    defaults = {
        "action": AuditAction.CONNECTION_ENROLLED,
        "actor": AuditActor.user(7, ip="203.0.113.4"),
        "subject": "retail_demo",
        "outcome": AuditOutcome.SUCCESS,
    }
    defaults.update(overrides)
    return AuditEvent(**defaults)


# ---------------------------------------------------------------------------------------------
# The record carries what an incident review needs
# ---------------------------------------------------------------------------------------------


def test_event_carries_actor_action_subject_outcome_and_timestamp():
    e = event()
    payload = e.as_record()
    assert payload["action"] == "connection.enrolled"
    assert payload["outcome"] == "success"
    assert payload["actor"] == {"kind": "user", "id": "7", "ip": "203.0.113.4"}
    assert payload["subject"] == "retail_demo"
    assert payload["timestamp"].endswith("+00:00")
    assert e.timestamp.tzinfo is not None


def test_naive_timestamp_is_pinned_to_utc_rather_than_left_ambiguous():
    e = event(timestamp=datetime(2026, 8, 24, 12, 0, 0))
    assert e.timestamp.tzinfo is UTC


def test_event_ids_are_unique_so_two_identical_actions_stay_distinguishable():
    assert event().event_id != event().event_id


def test_every_action_and_outcome_serialises_to_its_string_value():
    for action in AuditAction:
        e = event(action=action, outcome=AuditOutcome.DENIED)
        assert e.as_record()["action"] == action.value
    for outcome in AuditOutcome:
        assert event(outcome=outcome).as_record()["outcome"] == outcome.value


# ---------------------------------------------------------------------------------------------
# ...and nothing else
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "db_password",
        "connection_string",
        "dsn",
        "api_key",
        "session_token",
        "authorization",
        "cookie",
        "client_secret",
    ],
)
def test_credential_shaped_keys_are_redacted_whatever_the_value(key):
    e = event(detail={key: "hunter2"})
    assert e.detail[key] == "<redacted>"
    assert "hunter2" not in e.to_json()


@pytest.mark.parametrize("key", ["rows", "results", "records", "sample_data", "result_set"])
def test_result_row_keys_are_redacted(key):
    e = event(detail={key: [{"ssn": "123-45-6789"}, {"ssn": "987-65-4321"}]})
    assert e.detail[key] == "<redacted>"
    assert "123-45-6789" not in e.to_json()


def test_a_list_under_an_innocent_key_is_reduced_to_its_shape():
    e = event(detail={"tables": [("public", "customers"), ("public", "orders")]})
    assert e.detail["tables"] == "<list: 2 items>"


def test_a_mapping_under_an_innocent_key_is_reduced_to_its_shape():
    e = event(detail={"row": {"ssn": "123-45-6789"}})
    # `row` is not in the forbidden list, so the shape rule is what protects the value here.
    assert e.detail["row"] == "<mapping: 1 keys>"
    assert "123-45-6789" not in e.to_json()


def test_a_dsn_under_an_innocent_key_is_redacted_by_shape():
    e = event(detail={"target": DSN})
    assert e.detail["target"] == "<redacted>"
    assert "hunter2" not in e.to_json()


def test_a_bearer_header_value_is_redacted():
    e = event(detail={"header": "Bearer abcdef0123456789"})
    assert e.detail["header"] == "<redacted>"


def test_ciphertext_is_redacted_even_though_it_is_already_encrypted():
    # Ciphertext in an audit log is still a copy of a secret and still ages badly.
    assert event(detail={"stored": "enc:v1:gAAAAA"}).detail["stored"] == "<redacted>"


def test_subject_cannot_smuggle_a_dsn():
    assert event(subject=DSN).subject == "<redacted>"


def test_actor_id_cannot_smuggle_a_dsn():
    assert AuditActor(kind=ActorKind.API_KEY, id=DSN).id == "<redacted>"


def test_long_values_are_truncated_rather_than_unbounded():
    e = event(detail={"note": "x" * 5000})
    assert len(str(e.detail["note"])) <= 220


def test_detail_key_count_is_bounded_and_the_omission_is_recorded():
    e = event(detail={f"k{i}": i for i in range(50)})
    assert len(e.detail) == 21  # 20 keys + the _truncated marker
    assert e.detail["_truncated"] == "30 more keys omitted"


def test_scalars_survive_intact_so_the_record_stays_useful():
    e = event(detail={"row_count": 42, "truncated": True, "elapsed": 1.5, "missing": None})
    assert e.detail == {"row_count": 42, "truncated": True, "elapsed": 1.5, "missing": None}


def test_sanitize_detail_is_usable_on_its_own():
    assert sanitize_detail({"password": "x", "n": 1}) == {"n": 1, "password": "<redacted>"}
    assert sanitize_detail(None) == {}


# ---------------------------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------------------------


def test_logging_sink_emits_one_parseable_json_object_per_event(caplog):
    sink = LoggingAuditSink()
    with caplog.at_level(logging.INFO, logger="dbwhisper.audit"):
        sink.emit(event(detail={"row_count": 3}))
    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["action"] == "connection.enrolled"
    assert payload["detail"]["row_count"] == 3


def test_in_memory_sink_collects_and_filters_by_action():
    sink = InMemoryAuditSink()
    sink.emit(event(action=AuditAction.POLICY_DENIED, outcome=AuditOutcome.DENIED))
    sink.emit(event(action=AuditAction.DATA_EXPORTED))
    assert len(sink.events) == 2
    assert len(sink.of(AuditAction.POLICY_DENIED)) == 1


def test_in_memory_sink_accessor_returns_a_copy_so_history_cannot_be_rewritten():
    sink = InMemoryAuditSink()
    sink.emit(event())
    snapshot = sink.events
    assert isinstance(snapshot, tuple)
    sink.emit(event(action=AuditAction.SECRET_ROTATED))
    assert len(snapshot) == 1 and len(sink.events) == 2


def test_in_memory_sink_bounds_memory_and_counts_what_it_dropped():
    sink = InMemoryAuditSink(max_events=3)
    for _ in range(10):
        sink.emit(event())
    assert len(sink.events) == 3
    assert sink.dropped == 7


def test_composite_sink_fans_out():
    a, b = InMemoryAuditSink(), InMemoryAuditSink()
    composite_sink(a, b).emit(event())
    assert len(a.events) == len(b.events) == 1


# ---------------------------------------------------------------------------------------------
# The record() entry point
# ---------------------------------------------------------------------------------------------


def test_record_emits_to_the_active_sink_and_returns_the_event():
    sink = InMemoryAuditSink()
    with audit_to(sink):
        returned = record(
            AuditAction.TENANT_ACCESS_DENIED,
            subject="crm_db",
            outcome=AuditOutcome.DENIED,
            actor=AuditActor.user(3),
            reason="owner mismatch",
        )
    assert sink.events == (returned,)
    assert returned.reason == "owner mismatch"


def test_audit_to_restores_the_previous_sink():
    original = get_audit_sink()
    with audit_to(InMemoryAuditSink()):
        pass
    assert get_audit_sink() is original


def test_set_audit_sink_returns_the_previous_sink():
    original = get_audit_sink()
    replacement = InMemoryAuditSink()
    try:
        assert set_audit_sink(replacement) is original
        assert get_audit_sink() is replacement
    finally:
        set_audit_sink(original)


def test_a_failing_sink_does_not_propagate_out_of_a_denial_path(caplog):
    class Broken:
        def emit(self, _event):
            raise RuntimeError("collector unreachable")

    with caplog.at_level(logging.ERROR):
        returned = record(
            AuditAction.POLICY_DENIED,
            subject="fingerprint:abc",
            outcome=AuditOutcome.DENIED,
            sink=Broken(),
        )
    assert returned.action is AuditAction.POLICY_DENIED
    assert any("Audit sink failed" in r.getMessage() for r in caplog.records)


def test_default_actor_is_anonymous_rather_than_absent():
    sink = InMemoryAuditSink()
    record(
        AuditAction.CSRF_REJECTED,
        subject="/query",
        outcome=AuditOutcome.DENIED,
        sink=sink,
    )
    assert sink.events[0].actor.kind is ActorKind.ANONYMOUS


def test_actor_constructors_set_the_kind_they_claim():
    assert AuditActor.user(1).kind is ActorKind.USER
    assert AuditActor.api_key("ci-key").kind is ActorKind.API_KEY
    assert AuditActor.anonymous().kind is ActorKind.ANONYMOUS
    assert AuditActor.system("enrollment-worker").kind is ActorKind.SYSTEM
