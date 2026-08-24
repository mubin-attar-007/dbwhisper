"""The single place that decides what an observability signal is allowed to carry.

DBWhisper spans describe work performed on somebody else's database. The attributes that would
be most convenient to attach - the question, the generated SQL, the rows that came back, the DSN
that was dialled - are exactly the ones that would turn a trace backend into an uncontrolled
second copy of the customer's data. So the rule here is inverted from the usual "redact anything
that looks secret":

    **An attribute is dropped unless its key is on a closed allowlist.**

A denylist alone fails the moment somebody adds an attribute nobody thought about; an allowlist
fails safe by construction. :data:`_FORBIDDEN_KEY` is therefore not the defence - it is a smoke
alarm. Instrumentation that tries to attach ``password`` cannot leak (the allowlist already
stopped it) but it is reported as ``rejected``, so the mistake surfaces as a log line and a
counter instead of passing silently.

Order matters and is load-bearing: allowlist first, denylist second. ``dbw.sql_fingerprint``
contains the substring ``sql`` and would trip the denylist if the checks ran the other way round.

Values are constrained too, because an allowlisted key can still be handed the wrong object.
Only scalars survive - a list or a mapping is refused outright, since that is the shape result
rows arrive in - strings are truncated, and a string that looks like a credential is replaced
even under an allowlisted key.

Nothing here imports the rest of the application: this module is a leaf so it can be reasoned
about, and tested, on its own.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

#: The only Python types OpenTelemetry accepts that we are willing to emit.
AttributeValue = str | bool | int | float

#: Long enough for a fingerprint or a version string, short enough that a stray sentence is
#: obviously mangled rather than quietly stored in full.
MAX_STRING_CHARS = 120

REDACTED = "[redacted]"


class SpanAttr(StrEnum):
    """Every attribute key DBWhisper may put on a span. Adding a member is a review decision.

    Grouped by what the key answers: which run, whose data (identifiers only, never content),
    which versioned component answered, what it cost, and what happened.
    """

    # --- Which run ---------------------------------------------------------------------------
    RUN_ID = "dbw.run_id"
    TRACE_ID = "dbw.trace_id"
    STEP = "dbw.step"
    NODE = "dbw.node"

    # --- Whose data (opaque identifiers; the org id is hashed, see hash_org_id) ---------------
    ORG_HASH = "dbw.org_hash"
    SOURCE_ID = "dbw.source_id"
    SNAPSHOT_ID = "dbw.snapshot_id"

    # --- Which versioned component answered ---------------------------------------------------
    PROVIDER = "dbw.provider"
    MODEL = "dbw.model"
    CAPABILITY = "dbw.capability"
    PROMPT_VERSION = "dbw.prompt_version"
    POLICY_VERSION = "dbw.policy_version"
    REGISTRY_VERSION = "dbw.registry_version"
    EMBEDDING_VERSION = "dbw.embedding_version"

    # --- What it cost --------------------------------------------------------------------------
    TOKENS_PROMPT = "dbw.tokens.prompt"
    TOKENS_COMPLETION = "dbw.tokens.completion"
    TOKENS_TOTAL = "dbw.tokens.total"
    LATENCY_MS = "dbw.latency_ms"
    RETRY_COUNT = "dbw.retry_count"
    CACHE_HIT = "dbw.cache_hit"

    # --- What happened -------------------------------------------------------------------------
    SQL_FINGERPRINT = "dbw.sql_fingerprint"
    POLICY_DECISION = "dbw.policy_decision"
    ERROR_CATEGORY = "dbw.error_category"
    OUTCOME = "dbw.outcome"
    DIALECT = "dbw.dialect"
    MODE = "dbw.mode"
    EGRESS_POLICY = "dbw.egress_policy"
    ROW_COUNT = "dbw.row_count"
    TRUNCATED = "dbw.truncated"
    #: How many attributes this span refused. Emitted by the span helper, not by callers.
    ATTRIBUTES_DROPPED = "dbw.attributes_dropped"


ALLOWED_KEYS: frozenset[str] = frozenset(a.value for a in SpanAttr)


class ErrorCategory(StrEnum):
    """A closed vocabulary for why something failed.

    Exception messages are never recorded (they routinely embed a DSN, a table name, or a value
    from a row), so the category is the entire error signal a trace or a metric gets. It is a
    closed set for the same reason a metric label is: it has to be groupable.
    """

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    PERMISSION = "permission"
    POLICY_BLOCKED = "policy_blocked"
    VALIDATION = "validation"
    PROVIDER = "provider"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


#: Exception *type* names (lowercased, substring match) mapped to a category. Only the class name
#: is inspected - never ``str(exc)`` - so no message text can reach a span through this path.
#: Order is significant: the first match wins, so narrower needles come first.
_ERROR_TYPE_HINTS: tuple[tuple[str, ErrorCategory], ...] = (
    ("timeout", ErrorCategory.TIMEOUT),
    ("cancel", ErrorCategory.CANCELLED),
    ("ratelimit", ErrorCategory.RATE_LIMITED),
    ("toomanyrequests", ErrorCategory.RATE_LIMITED),
    ("connection", ErrorCategory.CONNECTION),
    ("operationalerror", ErrorCategory.CONNECTION),
    ("permission", ErrorCategory.PERMISSION),
    ("forbidden", ErrorCategory.PERMISSION),
    ("unauthor", ErrorCategory.PERMISSION),
    ("policy", ErrorCategory.POLICY_BLOCKED),
    ("notfound", ErrorCategory.NOT_FOUND),
    ("provider", ErrorCategory.PROVIDER),
    ("validation", ErrorCategory.VALIDATION),
    ("valueerror", ErrorCategory.VALIDATION),
)


def classify_exception(exc: BaseException) -> ErrorCategory:
    """Map an exception to a category using its type name only."""
    name = type(exc).__name__.lower()
    for needle, category in _ERROR_TYPE_HINTS:
        if needle in name:
            return category
    return ErrorCategory.INTERNAL


#: Keys that must never appear. Unreachable while the allowlist holds - see the module docstring
#: for why it exists anyway.
_FORBIDDEN_KEY = re.compile(
    r"pass(word|wd)|secret|token|api[_-]?key|credential|auth|cookie|session"
    r"|dsn|conn(ection)?[_-]?(string|url)|url|uri|host|user|owner"
    r"|email|phone|ssn|address|name|question|prompt|message|content|body|payload"
    r"|sql|statement|query|row|record|sample|value|result|data",
    re.I,
)

#: A value that looks like a credential-bearing URL (``scheme://user:pass@host``) or a
#: ``key=value`` secret. Applied even to allowlisted keys - defence in depth, not the main gate.
_SECRETISH_VALUE = re.compile(
    r"[a-z][a-z0-9+.\-]*://[^\s/@]*:[^\s/@]*@"
    r"|(?:api[_-]?key|password|passwd|secret|token|authorization)\s*[=:]\s*\S",
    re.I,
)

#: Fallback salt. Documented as a shortener, not an anonymiser - see :func:`hash_org_id`.
_UNSALTED_MARKER = "dbwhisper/observability/org"


@dataclass(frozen=True, slots=True)
class ScrubResult:
    """What survived, and what did not, so callers can report on their own instrumentation."""

    attributes: dict[str, AttributeValue]
    dropped: tuple[str, ...]
    rejected: tuple[str, ...]
    redacted: tuple[str, ...]

    @property
    def refused(self) -> int:
        """How many supplied attributes did not make it onto the span."""
        return len(self.dropped) + len(self.rejected)


def _coerce(value: object) -> AttributeValue | None:
    """Return an emittable scalar, or ``None`` when the value has the wrong shape entirely."""
    # bool before int: ``isinstance(True, int)`` is True and would export as 1.
    if isinstance(value, bool):
        return value
    if isinstance(value, StrEnum):
        return _coerce(value.value)
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _SECRETISH_VALUE.search(text):
            return REDACTED
        return text[:MAX_STRING_CHARS]
    # Deliberately unreachable for lists/tuples/dicts/objects: a sequence is the shape result rows
    # and message histories arrive in, and ``str(obj)`` would smuggle them straight through.
    return None


def scrub_attributes(attributes: Mapping[str, object]) -> ScrubResult:
    """Apply the allowlist to a caller-supplied attribute mapping.

    Never raises: instrumentation must not be able to break the request it is describing.
    """
    kept: dict[str, AttributeValue] = {}
    dropped: list[str] = []
    rejected: list[str] = []
    redacted: list[str] = []

    for raw_key, raw_value in attributes.items():
        key = str(raw_key)
        if key not in ALLOWED_KEYS:
            # The denylist runs *after* the allowlist, so `dbw.sql_fingerprint` is never mistaken
            # for a leak of the SQL itself.
            (rejected if _FORBIDDEN_KEY.search(key) else dropped).append(key)
            continue
        coerced = _coerce(raw_value)
        if coerced is None:
            dropped.append(key)
            continue
        if coerced == REDACTED:
            redacted.append(key)
        kept[key] = coerced

    return ScrubResult(
        attributes=kept,
        dropped=tuple(dropped),
        rejected=tuple(rejected),
        redacted=tuple(redacted),
    )


def safe_attributes(attributes: Mapping[str, object]) -> dict[str, AttributeValue]:
    """:func:`scrub_attributes` for callers that only want the surviving attributes."""
    return scrub_attributes(attributes).attributes


def hash_org_id(org_id: str | int | None, *, salt: str | None = None) -> str | None:
    """Return a short, stable digest of a tenant identifier, or ``None`` for no tenant.

    Truthful caveat: organisation ids are enumerable (small integers or UUIDs from our own
    database). An *unsalted* digest is therefore a shortener, not an anonymiser - anyone holding
    the id list can rebuild the mapping. It still buys something real: the raw id never leaves the
    process, so a trace backend cannot be joined against customer records by accident. Pass
    ``salt`` - or configure ``DBW_SECRET_KEYS``, which is used automatically - to make the mapping
    unreproducible outside the deployment.
    """
    if org_id is None:
        return None
    text = str(org_id).strip()
    if not text:
        return None
    if salt is None:
        salt = _default_salt()
    digest = hashlib.blake2s(text.encode("utf-8"), key=salt.encode("utf-8")[:64], digest_size=8)
    return digest.hexdigest()


def _default_salt() -> str:
    """First configured secret key if there is one, else the documented constant."""
    try:  # Imported lazily: this module must stay importable without application config.
        from app.core.config import get_settings

        keys = get_settings().secret_keys_list
    except Exception:  # pragma: no cover - config is optional for a leaf helper
        keys = []
    return keys[0] if keys else _UNSALTED_MARKER


__all__ = [
    "ALLOWED_KEYS",
    "MAX_STRING_CHARS",
    "REDACTED",
    "AttributeValue",
    "ErrorCategory",
    "ScrubResult",
    "SpanAttr",
    "classify_exception",
    "hash_org_id",
    "safe_attributes",
    "scrub_attributes",
]
