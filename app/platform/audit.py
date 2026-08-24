"""An append-only record of the decisions a reviewer would want to reconstruct after an incident.

Application logs answer "what happened"; they are also verbose, unstructured and full of things we
deliberately redact. This module answers a narrower question - **who was allowed to do what, to
which subject, and what did the system decide** - for the handful of actions where the answer
matters: enrolling a data source, overriding the writable-connection refusal, approving or rejecting
a query that policy would not run unattended, a policy denial, a cross-tenant access denial, a
secret rotation, and an export of result data.

Three design decisions are worth stating, because they are what makes the record trustworthy:

* **The event is a value, not a log line.** :class:`AuditEvent` is a frozen dataclass with a closed
  set of actions (:class:`AuditAction`) and outcomes (:class:`AuditOutcome`). A caller cannot invent
  an action string, so the set of auditable actions is enumerable by reading this file.
* **Sanitisation happens in the constructor, not at the sink.** ``__post_init__`` rewrites ``detail``
  before the object exists in a usable form, so there is no way to build an event carrying a
  password, a DSN or a result row and then hand it to a sink. Credential-shaped keys are redacted;
  every value is coerced to a scalar, so a list of rows becomes ``"<list: 3 items>"`` and its
  contents cannot travel. See :func:`sanitize_detail` and ``tests/test_audit.py``.
* **Append-only means the API has no delete.** Sinks expose ``emit`` and nothing else. The in-memory
  sink used by tests returns a tuple copy of its buffer, so a caller cannot rewrite history through
  the accessor either. This is a property of *this* API; durable tamper-evidence (hash chaining, a
  write-once store) is not implemented and is recorded as residual risk in
  ``docs/v2/THREAT_MODEL.md``.

The default sink writes one JSON object per event to the ``dbwhisper.audit`` logger, which keeps
audit records separable from application logs by logger name without requiring a database table.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from app.utils.logger import sanitize_for_log

audit_logger = logging.getLogger("dbwhisper.audit")

MAX_DETAIL_KEYS = 20
MAX_DETAIL_VALUE_LEN = 200
REDACTED = "<redacted>"


class AuditAction(StrEnum):
    """The closed set of security-relevant actions worth reconstructing after the fact.

    ``CONNECTION_ENROLL_REFUSED`` and ``CSRF_REJECTED`` record *refusals* rather than successes: an
    attack that was blocked leaves no other trace, and "we have no record of it" is the failure mode
    audit logging exists to prevent.
    """

    CONNECTION_ENROLLED = "connection.enrolled"
    CONNECTION_ENROLL_REFUSED = "connection.enroll_refused"
    CONNECTION_WRITABLE_OVERRIDE = "connection.writable_override"
    QUERY_APPROVAL_GRANTED = "query.approval_granted"
    QUERY_APPROVAL_REJECTED = "query.approval_rejected"
    POLICY_DENIED = "policy.denied"
    TENANT_ACCESS_DENIED = "tenant.access_denied"
    SECRET_ROTATED = "secret.rotated"
    DATA_EXPORTED = "data.exported"
    CSRF_REJECTED = "csrf.rejected"


class AuditOutcome(StrEnum):
    """Whether the action completed, was refused by a control, or failed unexpectedly.

    ``DENIED`` and ``ERROR`` are kept apart on purpose: a control that refused is the system working,
    a control that raised is the system failing, and an incident review needs to tell them apart.
    """

    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class ActorKind(StrEnum):
    """How the caller was identified - which is also how much the ``id`` is worth."""

    USER = "user"  # an authenticated session; id is the user id
    API_KEY = "api_key"  # a shared static token; id is a key label, never the token
    ANONYMOUS = "anonymous"  # no credential presented
    SYSTEM = "system"  # a worker, a migration, a CLI maintenance command


@dataclass(frozen=True, slots=True)
class AuditActor:
    """Who acted.

    ``id`` must never be a credential. For :attr:`ActorKind.API_KEY` it is a label or key id, not the
    token itself. The actor sits outside ``detail``, so it is cleaned by :func:`_identifier` rather
    than by :func:`sanitize_detail` - which also means a numeric user id survives intact instead of
    being mistaken for a SQL literal.

    ``ip`` is personal data in most jurisdictions. It is retained because an audit record without a
    source address answers half the question, and it is named as such in the threat model rather
    than quietly collected.
    """

    kind: ActorKind = ActorKind.ANONYMOUS
    id: str | None = None
    ip: str | None = None

    def __post_init__(self) -> None:
        if self.id is not None:
            object.__setattr__(self, "id", _identifier(self.id))
        if self.ip is not None:
            object.__setattr__(self, "ip", _client_address(self.ip))

    @classmethod
    def user(cls, user_id: int | str, ip: str | None = None) -> AuditActor:
        return cls(kind=ActorKind.USER, id=str(user_id), ip=ip)

    @classmethod
    def api_key(cls, key_label: str, ip: str | None = None) -> AuditActor:
        return cls(kind=ActorKind.API_KEY, id=key_label, ip=ip)

    @classmethod
    def anonymous(cls, ip: str | None = None) -> AuditActor:
        return cls(kind=ActorKind.ANONYMOUS, id=None, ip=ip)

    @classmethod
    def system(cls, component: str) -> AuditActor:
        return cls(kind=ActorKind.SYSTEM, id=component, ip=None)

    def as_record(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "id": self.id, "ip": self.ip}


# Key names whose *value* is assumed to be a credential regardless of what it looks like. Matched as
# substrings of the lower-cased key, because "db_password" and "passwordHash" are both real.
_CREDENTIAL_KEY_MARKERS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "cookie",
    "credential",
    "private_key",
    "session",
    "dsn",
    "connection_string",
    "conn_str",
)

# Key names that would carry query results. Even a sanitized row is customer data, and an audit
# record is not the place for it: the statement fingerprint and the row count are enough to
# correlate an audit entry with the execution log without copying the data itself.
_ROW_KEY_MARKERS = (
    "rows",
    "records",
    "results",
    "result_set",
    "sample",
    "payload",
    "data",
)


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    markers = _CREDENTIAL_KEY_MARKERS + _ROW_KEY_MARKERS
    return any(marker in lowered for marker in markers)


def _looks_like_secret(value: str) -> bool:
    """A narrow last line of defence for values arriving under an innocent key.

    Deliberately narrow: it catches DSNs with embedded credentials, bearer headers and our own
    ``enc:v1:`` ciphertext, which are the shapes that have historically leaked into logs in this
    codebase. It is not a general secret scanner and nothing here treats it as one.
    """
    lowered = value.lower()
    if "://" in value and "@" in value:
        authority = value.split("://", 1)[1].split("@", 1)[0]
        if ":" in authority:
            return True
    return lowered.startswith("bearer ") or value.startswith("enc:v1:")


_MAX_IDENTIFIER_LEN = 128
_ADDRESS_TAIL = re.compile(r"[:%](?:\d{1,5}|[A-Za-z0-9._-]{1,32})$")


def _identifier(value: Any) -> str:
    """Clean a value that is an identifier - a ``db_flag``, a user id, a route, a fingerprint.

    Deliberately **not** :func:`~app.utils.logger.sanitize_for_log`: that helper masks runs of three
    or more digits because it is written for SQL text, and an audit subject of ``store_2024`` or a
    user id of ``4711`` is not a literal to hide. Secret-shaped values are still redacted.
    """
    text = value if isinstance(value, str) else str(value)
    if _looks_like_secret(text):
        return REDACTED
    return text.strip()[:_MAX_IDENTIFIER_LEN]


def _client_address(value: Any) -> str:
    """Keep a source address usable.

    An IP that has been through numeric-literal masking (``<NUM>.0.<NUM>.4``) cannot be correlated
    with anything, which defeats the reason for recording it. Values that parse as an address are
    kept verbatim; anything else - a spoofed ``X-Forwarded-For`` chain, a hostname, junk - is
    truncated and marked, because an unparseable address is itself worth seeing.
    """
    text = (value if isinstance(value, str) else str(value)).strip()
    candidate = text.strip("[]")
    tail = _ADDRESS_TAIL.search(candidate)
    if tail is not None:
        candidate = candidate[: tail.start()]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return f"<unparsed:{text[:45]}>"
    return text[:64]


def _scalar(value: Any) -> str | int | float | bool | None:
    """Coerce one value to a scalar, so containers cannot smuggle content through.

    A list or mapping is replaced by its *shape*. That is the whole point: a caller who passes
    ``detail={"rows": [...]}`` gets a shape string and the rows stay where they were.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        return f"<mapping: {len(value)} keys>"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"<{type(value).__name__}: {len(value)} items>"
    text = value if isinstance(value, str) else str(value)
    if _looks_like_secret(text):
        return REDACTED
    return sanitize_for_log(text, max_len=MAX_DETAIL_VALUE_LEN)


def sanitize_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a detail mapping safe to persist: no credentials, no result rows, scalars only.

    Forbidden keys keep their key with a ``<redacted>`` value rather than disappearing, because "a
    password field was here and we removed it" is more useful to a reviewer than silence.
    """
    if not detail:
        return {}
    out: dict[str, Any] = {}
    for key in sorted(detail):
        if len(out) >= MAX_DETAIL_KEYS:
            out["_truncated"] = f"{len(detail) - MAX_DETAIL_KEYS} more keys omitted"
            break
        safe_key = str(key)[:64]
        out[safe_key] = REDACTED if _is_forbidden_key(safe_key) else _scalar(detail[key])
    return out


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One security-relevant decision, sanitised at construction time.

    ``subject`` is what was acted upon - a ``db_flag``, a statement fingerprint, a key id. It is a
    free string because subjects differ per action, but it goes through the same scalar coercion as
    the detail values, so a DSN cannot arrive through it either.
    """

    action: AuditAction
    actor: AuditActor
    subject: str
    outcome: AuditOutcome
    reason: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _identifier(self.subject))
        object.__setattr__(self, "reason", str(_scalar(self.reason) or ""))
        object.__setattr__(self, "detail", sanitize_detail(self.detail))
        if self.timestamp.tzinfo is None:
            # A naive timestamp is ambiguous the moment it is written down; assume UTC and say so.
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))

    def as_record(self) -> dict[str, Any]:
        """A JSON-serialisable dict with a fixed key order, so records diff cleanly."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.astimezone(UTC).isoformat(),
            "action": self.action.value,
            "outcome": self.outcome.value,
            "actor": self.actor.as_record(),
            "subject": self.subject,
            "reason": self.reason,
            "detail": dict(self.detail),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_record(), separators=(",", ":"), default=str)


class AuditSink(Protocol):
    """Where events go. Deliberately write-only: there is no read, update or delete."""

    def emit(self, event: AuditEvent) -> None: ...


class LoggingAuditSink:
    """The default: one JSON object per event on the ``dbwhisper.audit`` logger.

    A dedicated logger name (rather than the module logger) lets an operator route audit records to
    their own handler, file or collector without also shipping application debug output.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or audit_logger

    def emit(self, event: AuditEvent) -> None:
        self._logger.info(event.to_json())


class InMemoryAuditSink:
    """A sink for tests, and for demo mode where nothing should outlive the process.

    :attr:`events` returns a tuple copy, so a caller holding the sink cannot rewrite history through
    the accessor. ``max_events`` bounds memory; the oldest events are dropped and the drop is
    counted rather than hidden.
    """

    def __init__(self, max_events: int = 1000) -> None:
        self._events: list[AuditEvent] = []
        self._max = max(1, max_events)
        self.dropped = 0

    def emit(self, event: AuditEvent) -> None:
        self._events.append(event)
        while len(self._events) > self._max:
            self._events.pop(0)
            self.dropped += 1

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def of(self, action: AuditAction) -> tuple[AuditEvent, ...]:
        return tuple(e for e in self._events if e.action is action)


class _CompositeSink:
    def __init__(self, sinks: tuple[AuditSink, ...]) -> None:
        self._sinks = sinks

    def emit(self, event: AuditEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)


_sink: AuditSink = LoggingAuditSink()


def get_audit_sink() -> AuditSink:
    return _sink


def set_audit_sink(sink: AuditSink) -> AuditSink:
    """Replace the process-wide sink and return the previous one, so callers can restore it."""
    global _sink
    previous, _sink = _sink, sink
    return previous


def composite_sink(*sinks: AuditSink) -> AuditSink:
    """Fan one event out to several sinks (e.g. logs plus an in-memory buffer for a status page)."""
    return _CompositeSink(tuple(sinks))


@contextmanager
def audit_to(sink: AuditSink) -> Iterator[AuditSink]:
    """Temporarily route audit events to ``sink``. Restores the previous sink in a ``finally``."""
    previous = set_audit_sink(sink)
    try:
        yield sink
    finally:
        set_audit_sink(previous)


def record(
    action: AuditAction,
    *,
    subject: str,
    outcome: AuditOutcome,
    actor: AuditActor | None = None,
    reason: str = "",
    detail: Mapping[str, Any] | None = None,
    sink: AuditSink | None = None,
) -> AuditEvent:
    """Build, sanitise and emit one event; return it so a caller can correlate or assert on it.

    Emission never raises. A broken sink must not turn a successful request into a 500, and - more
    importantly - must not turn a *denial* into an allow by raising out of the denial path. The
    failure is reported on the application logger so it stays visible.
    """
    event = AuditEvent(
        action=action,
        actor=actor or AuditActor(),
        subject=subject,
        outcome=outcome,
        reason=reason,
        detail=detail or {},
    )
    target = sink or _sink
    try:
        target.emit(event)
    except Exception:  # pragma: no cover - defensive; a sink failure must not break the request
        logging.getLogger(__name__).exception("Audit sink failed for action=%s", action.value)
    return event


__all__ = [
    "ActorKind",
    "AuditAction",
    "AuditActor",
    "AuditEvent",
    "AuditOutcome",
    "AuditSink",
    "InMemoryAuditSink",
    "LoggingAuditSink",
    "audit_to",
    "composite_sink",
    "get_audit_sink",
    "record",
    "sanitize_detail",
    "set_audit_sink",
]
