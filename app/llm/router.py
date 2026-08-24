"""Capability-aware routing with a circuit breaker.

The v1 behaviour was "first configured API key wins, then try the next one on any exception". That
has three problems this module fixes: it cannot express *what the caller needs*, it re-dials a
provider that is known to be down on every single request, and it never records which provider
actually answered.

Routing is a filter followed by a sort:

* **Filters** (hard): the profile must have every requested capability; a remote profile is
  excluded outright when the egress policy is ``LOCAL_ONLY``; a profile whose breaker is open is
  skipped until its cooldown expires.
* **Sort** (soft): local first, then healthy-and-recently-successful, then quality hint, then
  observed latency. Cost is used only when a deployment configures it.

Every attempt - success or failure - is recorded in a :class:`~app.llm.types.RoutingDecision` so a
trace can answer "why did this run use that model?".
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.llm.registry import EnabledProfiles, enabled_profiles, registry_version
from app.llm.structured import complete_structured
from app.llm.types import (
    AttemptRecord,
    FailureKind,
    ModelCapability,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderKind,
    RoutingDecision,
)
from app.platform.modes import EgressPolicy

logger = logging.getLogger(__name__)

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60.0


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class CircuitBreaker:
    """One breaker per provider. Open means "stop calling this for a while"."""

    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    failures: int = 0
    opened_at: float | None = None
    last_failure: FailureKind | None = None
    successes: int = 0
    latency_ms_ewma: float | None = None

    def state(self, now: float | None = None) -> BreakerState:
        if self.opened_at is None:
            return BreakerState.CLOSED
        moment = now if now is not None else time.monotonic()
        if moment - self.opened_at >= self.cooldown_seconds:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allows(self, now: float | None = None) -> bool:
        return self.state(now) is not BreakerState.OPEN

    def record_success(self, latency_ms: float) -> None:
        self.failures = 0
        self.opened_at = None
        self.last_failure = None
        self.successes += 1
        self.latency_ms_ewma = (
            latency_ms
            if self.latency_ms_ewma is None
            else 0.7 * self.latency_ms_ewma + 0.3 * latency_ms
        )

    def record_failure(self, kind: FailureKind, now: float | None = None) -> None:
        self.last_failure = kind
        if not kind.should_open_breaker:
            return  # a bad request is the caller's fault, not the provider's
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = now if now is not None else time.monotonic()


ProviderFactory = Callable[[ModelProfile], object]


@dataclass
class ModelRouter:
    """Chooses a profile per request and falls through on failure."""

    provider_factory: ProviderFactory
    available: EnabledProfiles = field(default_factory=lambda: enabled_profiles("auto"))
    prefer_local: bool = True
    cost_weight: float = 0.0
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    # -- breaker access ------------------------------------------------------------------------
    def breaker(self, provider: str) -> CircuitBreaker:
        return self.breakers.setdefault(provider, CircuitBreaker())

    def breaker_states(self) -> dict[str, str]:
        return {name: b.state().value for name, b in self.breakers.items()}

    # -- selection -----------------------------------------------------------------------------
    def candidates(
        self,
        capabilities: frozenset[ModelCapability] | set[ModelCapability],
        egress: EgressPolicy,
    ) -> tuple[list[ModelProfile], dict[str, str]]:
        rejected: dict[str, str] = {}
        eligible: list[ModelProfile] = []
        for candidate in self.available.profiles:
            if not candidate.satisfies(capabilities):
                missing = sorted(c.value for c in set(capabilities) - candidate.capabilities)
                rejected[candidate.name] = f"missing capability: {', '.join(missing)}"
                continue
            if candidate.kind is ProviderKind.REMOTE and egress is EgressPolicy.LOCAL_ONLY:
                rejected[candidate.name] = "egress policy is LOCAL_ONLY"
                continue
            if not self.breaker(candidate.provider).allows():
                rejected[candidate.name] = f"circuit breaker open for {candidate.provider}"
                continue
            eligible.append(candidate)

        eligible.sort(key=self._sort_key)
        return eligible, rejected

    def _sort_key(self, candidate: ModelProfile) -> tuple:
        breaker = self.breaker(candidate.provider)
        local_rank = 0 if (self.prefer_local and candidate.is_local) else 1
        half_open_rank = 1 if breaker.state() is BreakerState.HALF_OPEN else 0
        latency = breaker.latency_ms_ewma if breaker.latency_ms_ewma is not None else 0.0
        cost = 0.0
        if self.cost_weight and candidate.cost_per_mtok_out is not None:
            cost = candidate.cost_per_mtok_out * self.cost_weight
        return (local_rank, half_open_rank, -candidate.quality_hint, cost, latency)

    # -- execution -----------------------------------------------------------------------------
    def complete(
        self, request: ModelRequest, *, max_attempts: int = 3
    ) -> tuple[ModelResponse, RoutingDecision]:
        """Run the request on the best profile, falling through on retryable failures."""
        eligible, rejected = self.candidates(request.capabilities, request.egress_policy)
        decision = RoutingDecision(
            chosen=None, candidates=[p.name for p in eligible], rejected=rejected
        )
        if not eligible:
            raise ProviderError(
                FailureKind.BAD_REQUEST,
                _no_candidate_message(request, rejected),
            )

        last: ProviderError | None = None
        for index, candidate in enumerate(eligible[:max_attempts]):
            provider = self.provider_factory(candidate)
            started = time.perf_counter()
            try:
                if request.json_schema:
                    response = complete_structured(provider, request, candidate)
                else:
                    response = provider.complete(request, candidate)
            except ProviderError as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                self.breaker(candidate.provider).record_failure(exc.kind)
                decision.attempts.append(
                    AttemptRecord(
                        candidate.name,
                        candidate.provider,
                        False,
                        str(exc)[:160],
                        exc.kind,
                        latency_ms,
                    )
                )
                logger.warning(
                    "Model profile %s failed (%s); %s",
                    candidate.name,
                    exc.kind.value,
                    "trying the next profile"
                    if index + 1 < len(eligible[:max_attempts])
                    else "no profiles left",
                )
                last = exc
                continue
            except Exception as exc:  # an adapter that leaked a raw SDK error
                latency_ms = (time.perf_counter() - started) * 1000
                self.breaker(candidate.provider).record_failure(FailureKind.UNKNOWN)
                decision.attempts.append(
                    AttemptRecord(
                        candidate.name,
                        candidate.provider,
                        False,
                        f"{type(exc).__name__}: {exc}"[:160],
                        FailureKind.UNKNOWN,
                        latency_ms,
                    )
                )
                last = ProviderError(FailureKind.UNKNOWN, str(exc), provider=candidate.provider)
                continue

            latency_ms = response.latency_ms or (time.perf_counter() - started) * 1000
            self.breaker(candidate.provider).record_success(latency_ms)
            decision.attempts.append(
                AttemptRecord(candidate.name, candidate.provider, True, latency_ms=latency_ms)
            )
            decision.chosen = candidate.name
            decision.fallback_used = index > 0
            return response, decision

        raise last or ProviderError(FailureKind.UNKNOWN, "No model profile produced a response")

    # -- diagnostics ---------------------------------------------------------------------------
    def describe(self) -> dict[str, object]:
        return {
            "registry_version": registry_version(),
            "enabled_profiles": self.available.names(),
            "excluded": self.available.reasons,
            "breakers": self.breaker_states(),
        }


def _no_candidate_message(request: ModelRequest, rejected: dict[str, str]) -> str:
    wanted = ", ".join(sorted(c.value for c in request.capabilities)) or "any capability"
    if not rejected:
        return (
            f"No model profile is enabled for {wanted}. Start a local model server "
            "(ollama serve) or set MODEL_PROFILE=fake for offline testing."
        )
    detail = "; ".join(f"{name}: {reason}" for name, reason in list(rejected.items())[:6])
    return f"No model profile satisfies {wanted}. Excluded - {detail}"


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_FAILURE_THRESHOLD",
    "BreakerState",
    "CircuitBreaker",
    "ModelRouter",
]
