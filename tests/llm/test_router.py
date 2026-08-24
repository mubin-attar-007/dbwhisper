"""Capability routing, fallback, the circuit breaker and egress enforcement."""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.llm.providers.fake import FakeProvider
from app.llm.registry import (
    ALL_PROFILES,
    FAKE_PROFILE,
    LOCAL_PROFILES,
    enabled_profiles,
    profile,
    registry_version,
)
from app.llm.router import BreakerState, CircuitBreaker, ModelRouter
from app.llm.types import (
    FailureKind,
    ModelCapability,
    ModelProfile,
    ModelRequest,
    ProviderError,
    ProviderKind,
)
from app.platform.modes import EgressPolicy

C = ModelCapability


def _profile(
    name: str, provider: str, kind: ProviderKind, quality: int = 50, **kwargs
) -> ModelProfile:
    return ModelProfile(
        name=name,
        provider=provider,
        model=f"{name}-model",
        kind=kind,
        capabilities=kwargs.pop("capabilities", frozenset({C.SQL_GENERATION, C.SUMMARIZATION})),
        quality_hint=quality,
        **kwargs,
    )


LOCAL = _profile("local-a", "ollama", ProviderKind.LOCAL, quality=40)
LOCAL_BETTER = _profile("local-b", "ollama", ProviderKind.LOCAL, quality=70)
REMOTE = _profile("remote-a", "openai", ProviderKind.REMOTE, quality=90)
EMBEDDER = _profile("embedder", "ollama", ProviderKind.LOCAL, capabilities=frozenset({C.EMBEDDING}))


def _router(profiles, providers: dict[str, object] | None = None) -> ModelRouter:
    from app.llm.registry import EnabledProfiles

    registry = providers if providers is not None else {}

    def factory(p: ModelProfile):
        return registry.setdefault(p.provider, FakeProvider(name=p.provider))

    return ModelRouter(provider_factory=factory, available=EnabledProfiles(tuple(profiles), {}))


def _request(**kwargs) -> ModelRequest:
    params = {
        "system": None,
        "user": "question",
        "capabilities": frozenset({C.SQL_GENERATION}),
        "egress_policy": EgressPolicy.REMOTE_ALLOWED,
        "prompt_name": "test",
    }
    params.update(kwargs)
    return ModelRequest(**params)


class TestCandidateSelection:
    def test_capability_filter(self):
        router = _router([LOCAL, EMBEDDER])
        eligible, rejected = router.candidates({C.SQL_GENERATION}, EgressPolicy.REMOTE_ALLOWED)
        assert [p.name for p in eligible] == ["local-a"]
        assert "missing capability" in rejected["embedder"]

    def test_local_is_preferred_over_a_better_remote(self):
        router = _router([REMOTE, LOCAL])
        eligible, _ = router.candidates({C.SQL_GENERATION}, EgressPolicy.REMOTE_ALLOWED)
        assert [p.name for p in eligible] == ["local-a", "remote-a"]

    def test_quality_breaks_ties_within_a_tier(self):
        router = _router([LOCAL, LOCAL_BETTER])
        eligible, _ = router.candidates({C.SQL_GENERATION}, EgressPolicy.REMOTE_ALLOWED)
        assert [p.name for p in eligible] == ["local-b", "local-a"]

    def test_local_only_egress_excludes_remote(self):
        router = _router([LOCAL, REMOTE])
        eligible, rejected = router.candidates({C.SQL_GENERATION}, EgressPolicy.LOCAL_ONLY)
        assert [p.name for p in eligible] == ["local-a"]
        assert rejected["remote-a"] == "egress policy is LOCAL_ONLY"

    def test_no_candidate_raises_with_actionable_advice(self):
        router = _router([REMOTE])
        with pytest.raises(ProviderError) as exc_info:
            router.complete(_request(egress_policy=EgressPolicy.LOCAL_ONLY))
        assert "LOCAL_ONLY" in str(exc_info.value)


class TestExecutionAndFallback:
    def test_successful_call_records_the_choice(self):
        router = _router([LOCAL])
        response, decision = router.complete(_request())
        assert decision.chosen == "local-a"
        assert decision.fallback_used is False
        assert response.provider == "ollama"
        assert [a.ok for a in decision.attempts] == [True]

    def test_falls_through_to_the_next_profile(self):
        providers: dict[str, object] = {}
        router = _router([LOCAL, REMOTE], providers)
        router.provider_factory(LOCAL)  # materialise so we can script it
        providers["ollama"].fail_next(FailureKind.SERVER_ERROR, "local server died")

        response, decision = router.complete(_request())
        assert decision.chosen == "remote-a"
        assert decision.fallback_used is True
        assert decision.attempts[0].failure is FailureKind.SERVER_ERROR
        assert response.provider == "openai"

    def test_all_profiles_failing_raises_the_last_error(self):
        providers: dict[str, object] = {}
        router = _router([LOCAL, REMOTE], providers)
        router.provider_factory(LOCAL)
        router.provider_factory(REMOTE)
        providers["ollama"].fail_next(FailureKind.CONNECTION)
        providers["openai"].fail_next(FailureKind.RATE_LIMIT)
        with pytest.raises(ProviderError) as exc_info:
            router.complete(_request())
        assert exc_info.value.kind is FailureKind.RATE_LIMIT

    def test_max_attempts_is_respected(self):
        providers: dict[str, object] = {}
        router = _router([LOCAL, LOCAL_BETTER, REMOTE], providers)
        for p in (LOCAL, REMOTE):
            router.provider_factory(p)
        providers["ollama"].fail_next(FailureKind.SERVER_ERROR, times=2)
        with pytest.raises(ProviderError):
            router.complete(_request(), max_attempts=2)

    def test_an_adapter_leaking_a_raw_exception_is_still_handled(self):
        class Broken:
            name: ClassVar[str] = "broken"

            def complete(self, request, profile):
                raise ValueError("vendor SDK exploded")

            def health(self):  # pragma: no cover - unused
                return None

        from app.llm.registry import EnabledProfiles

        router = ModelRouter(
            provider_factory=lambda p: Broken() if p is LOCAL else FakeProvider(name=p.provider),
            available=EnabledProfiles((LOCAL, REMOTE), {}),
        )
        _response, decision = router.complete(_request())
        assert decision.chosen == "remote-a"
        assert decision.attempts[0].failure is FailureKind.UNKNOWN


class TestCircuitBreaker:
    def test_opens_after_the_threshold(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=30)
        assert breaker.state() is BreakerState.CLOSED
        breaker.record_failure(FailureKind.CONNECTION, now=100.0)
        assert breaker.state(now=100.0) is BreakerState.CLOSED
        breaker.record_failure(FailureKind.CONNECTION, now=101.0)
        assert breaker.state(now=101.0) is BreakerState.OPEN
        assert not breaker.allows(now=101.0)

    def test_half_opens_after_the_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        breaker.record_failure(FailureKind.SERVER_ERROR, now=100.0)
        assert breaker.state(now=120.0) is BreakerState.OPEN
        assert breaker.state(now=131.0) is BreakerState.HALF_OPEN
        assert breaker.allows(now=131.0)

    def test_success_closes_it(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure(FailureKind.CONNECTION, now=100.0)
        breaker.record_success(latency_ms=25.0)
        assert breaker.state() is BreakerState.CLOSED
        assert breaker.latency_ms_ewma == 25.0

    def test_a_bad_request_does_not_open_the_breaker(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure(FailureKind.BAD_REQUEST)
        breaker.record_failure(FailureKind.SCHEMA_VIOLATION)
        assert breaker.state() is BreakerState.CLOSED, (
            "our own bad prompt is not the provider's fault"
        )

    def test_router_skips_a_provider_with_an_open_breaker(self):
        providers: dict[str, object] = {}
        router = _router([LOCAL, REMOTE], providers)
        router.provider_factory(LOCAL)
        providers["ollama"].fail_next(FailureKind.CONNECTION, times=3)
        for _ in range(3):
            router.complete(_request())  # each falls through to remote

        eligible, rejected = router.candidates({C.SQL_GENERATION}, EgressPolicy.REMOTE_ALLOWED)
        assert [p.name for p in eligible] == ["remote-a"]
        assert "circuit breaker open" in rejected["local-a"]
        assert router.breaker_states()["ollama"] == "open"


class TestRegistry:
    def test_local_profiles_need_no_api_key(self):
        available = enabled_profiles("auto", env={})
        assert available.names() == [p.name for p in LOCAL_PROFILES]
        assert all(p.is_local for p in available.profiles)

    def test_remote_profiles_appear_only_with_a_key(self):
        assert "openai-gpt4o" not in enabled_profiles("auto", env={}).names()
        assert "openai-gpt4o" in enabled_profiles("auto", env={"OPENAI_API_KEY": "k"}).names()

    def test_reasons_explain_every_exclusion(self):
        available = enabled_profiles("auto", env={})
        for name in ("openai-gpt4o", "groq-qwen", "fake"):
            assert available.reasons.get(name)

    def test_fake_selection_is_exclusive(self):
        assert enabled_profiles("fake").names() == ["fake"]

    def test_pinning_an_unavailable_remote_profile_fails_loudly(self):
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            enabled_profiles("openai-gpt4o", env={})

    def test_pinning_a_local_profile_works_offline(self):
        assert enabled_profiles("local-small", env={}).names() == ["local-small"]

    def test_registry_version_is_stable_and_specific(self):
        assert registry_version() == registry_version()
        assert registry_version().startswith("models@")

    def test_every_profile_is_reachable_by_name(self):
        for p in ALL_PROFILES:
            assert profile(p.name) is p
        with pytest.raises(KeyError):
            profile("no-such-profile")

    def test_fake_profile_is_never_auto_enabled(self):
        assert (
            FAKE_PROFILE.name not in enabled_profiles("auto", env={"OPENAI_API_KEY": "k"}).names()
        )
