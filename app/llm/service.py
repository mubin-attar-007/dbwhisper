"""Process-wide access to the model layer, built from settings.

Callers ask :func:`get_router` for a router and hand it a :class:`~app.llm.types.ModelRequest`.
Nothing above this module constructs a provider, so switching a deployment from a hosted API to a
local model is a change to ``MODEL_PROFILE`` and ``OLLAMA_BASE_URL``, not a change to any code that
uses a model.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.llm.providers.fake import FakeProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.remote import RemoteProvider
from app.llm.registry import enabled_profiles
from app.llm.router import ModelRouter
from app.llm.types import ModelProfile, ProviderHealth, ProviderKind
from app.platform.modes import EgressPolicy

logger = logging.getLogger(__name__)

_providers: dict[str, Any] = {}


def build_provider(profile: ModelProfile) -> Any:
    """One provider instance per provider name, reused across profiles."""
    if profile.provider in _providers:
        return _providers[profile.provider]

    settings = get_settings()
    if profile.kind is ProviderKind.FAKE:
        provider: Any = FakeProvider()
    elif profile.provider == "ollama":
        provider = OllamaProvider(base_url=settings.ollama_base_url)
    else:
        provider = RemoteProvider(name=profile.provider)
    _providers[profile.provider] = provider
    return provider


@lru_cache(maxsize=1)
def get_router() -> ModelRouter:
    """The router for this process, configured from settings."""
    settings = get_settings()
    egress = settings.effective_egress_policy
    allow_remote = (
        settings.mode_policy.allow_remote_providers and egress is not EgressPolicy.LOCAL_ONLY
    )
    available = enabled_profiles(settings.model_profile, allow_remote=allow_remote)
    if not available.profiles:
        logger.warning(
            "No model profile is enabled. Start a local model server or set MODEL_PROFILE=fake."
        )
    return ModelRouter(provider_factory=build_provider, available=available)


def reset() -> None:
    """Drop cached providers and the router (used by tests and after a config change)."""
    _providers.clear()
    get_router.cache_clear()


def health_report() -> dict[str, ProviderHealth]:
    """Liveness of every provider backing an enabled profile."""
    router = get_router()
    seen: dict[str, ProviderHealth] = {}
    for profile in router.available.profiles:
        if profile.provider in seen:
            continue
        try:
            seen[profile.provider] = build_provider(profile).health()
        except Exception as exc:  # pragma: no cover - defensive
            seen[profile.provider] = ProviderHealth(
                provider=profile.provider, available=False, detail=f"{type(exc).__name__}: {exc}"
            )
    return seen


__all__ = ["build_provider", "get_router", "health_report", "reset"]
