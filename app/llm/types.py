"""The vocabulary of the model layer.

A graph node never asks for "Groq" or "gpt-4o". It asks for a *capability* - "I need reliable
structured JSON, and this data may not leave the host" - and the router answers with whatever
profile satisfies it. That indirection is what lets DBWhisper run entirely on a local model with no
API key, and it is why nothing above this package imports a provider SDK.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.platform.modes import EgressPolicy


class ModelCapability(StrEnum):
    """What a caller needs from a model. Profiles declare which of these they support."""

    STRUCTURED_JSON = "structured_json"
    TOOL_CALLING = "tool_calling"
    SQL_GENERATION = "sql_generation"
    SUMMARIZATION = "summarization"
    CLASSIFICATION = "classification"
    LONG_CONTEXT = "long_context"
    STREAMING = "streaming"
    LOCAL_EXECUTION = "local_execution"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class ProviderKind(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"
    FAKE = "fake"

    @property
    def is_local(self) -> bool:
        return self in (ProviderKind.LOCAL, ProviderKind.FAKE)


class FailureKind(StrEnum):
    """Why a call failed, normalised across providers so the router can act on it."""

    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    BAD_REQUEST = "bad_request"
    SERVER_ERROR = "server_error"
    CONTENT_FILTER = "content_filter"
    SCHEMA_VIOLATION = "schema_violation"
    UNKNOWN = "unknown"

    @property
    def is_retryable(self) -> bool:
        return self in (
            FailureKind.RATE_LIMIT,
            FailureKind.TIMEOUT,
            FailureKind.CONNECTION,
            FailureKind.SERVER_ERROR,
        )

    @property
    def should_open_breaker(self) -> bool:
        """Auth and connection failures will not fix themselves within a request."""
        return self in (
            FailureKind.AUTH,
            FailureKind.CONNECTION,
            FailureKind.SERVER_ERROR,
            FailureKind.RATE_LIMIT,
        )


class ProviderError(RuntimeError):
    def __init__(
        self,
        kind: FailureKind,
        message: str,
        *,
        provider: str = "",
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """A named, versioned model configuration. Profiles are code, not database rows."""

    name: str
    provider: str
    model: str
    kind: ProviderKind
    capabilities: frozenset[ModelCapability]
    context_tokens: int = 8192
    max_output_tokens: int = 2048
    temperature: float = 0.1
    supports_native_json_schema: bool = False
    #: Rough quality ranking for the tasks this profile is meant for (0-100, hand-set, not measured).
    #: Used only to break ties between profiles that all satisfy the request.
    quality_hint: int = 50
    #: Optional cost per million tokens, for deployments that want cost-aware routing.
    cost_per_mtok_in: float | None = None
    cost_per_mtok_out: float | None = None
    notes: str = ""

    def satisfies(self, required: frozenset[ModelCapability] | set[ModelCapability]) -> bool:
        return set(required).issubset(self.capabilities)

    @property
    def is_local(self) -> bool:
        return self.kind.is_local


@dataclass(slots=True)
class ModelRequest:
    """One call to a model. Prompts are already rendered; the model layer does not template."""

    system: str | None
    user: str
    capabilities: frozenset[ModelCapability] = frozenset()
    json_schema: dict[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    stop: list[str] = field(default_factory=list)
    egress_policy: EgressPolicy = EgressPolicy.LOCAL_ONLY
    prompt_name: str = "unnamed"
    prompt_version: str = "0"
    timeout_seconds: float = 60.0

    def messages(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if self.system:
            out.append({"role": "system", "content": self.system})
        out.append({"role": "user", "content": self.user})
        return out


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class ModelResponse:
    text: str
    profile: str
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    parsed: Any = None
    repair_attempts: int = 0
    finish_reason: str | None = None

    @property
    def is_structured(self) -> bool:
        return self.parsed is not None


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    available: bool
    detail: str = ""
    latency_ms: float | None = None
    models: list[str] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class AttemptRecord:
    profile: str
    provider: str
    ok: bool
    reason: str = ""
    failure: FailureKind | None = None
    latency_ms: float = 0.0


@dataclass(slots=True)
class RoutingDecision:
    """Why this profile answered - recorded on the run so a trace can explain the choice."""

    chosen: str | None
    candidates: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    attempts: list[AttemptRecord] = field(default_factory=list)
    fallback_used: bool = False

    def summary(self) -> str:
        if self.chosen is None:
            return "no profile satisfied the request"
        if self.fallback_used:
            failed = ", ".join(
                f"{a.profile} ({a.failure or a.reason})" for a in self.attempts if not a.ok
            )
            return f"{self.chosen} after fallback from {failed}"
        return self.chosen


__all__ = [
    "AttemptRecord",
    "FailureKind",
    "ModelCapability",
    "ModelProfile",
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "ProviderHealth",
    "ProviderKind",
    "RoutingDecision",
    "TokenUsage",
]
