"""Versioned model profiles, defined in code so every run can record exactly what answered.

Profiles are *not* database rows. A model configuration that can drift without review is a
configuration nobody can reproduce six months later when asking why an evaluation number changed;
here the set of profiles is a constant with a version hash, and the environment only chooses which
of them are enabled.

The default local profiles below are a **starting point, not a benchmark result**. Which one becomes
the default is decided by ``eval-model-matrix`` on the target hardware - see
``docs/v2/IMPLEMENTATION_ROADMAP.md``, Phase 8.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from app.llm.types import ModelCapability, ModelProfile, ProviderKind

C = ModelCapability

_TEXT = frozenset({C.SUMMARIZATION, C.CLASSIFICATION})
_SQL = frozenset({C.SQL_GENERATION, C.STRUCTURED_JSON, C.SUMMARIZATION, C.CLASSIFICATION})

#: Local, Apache-2.0 models that run without an API key. Sizes are the quantised defaults Ollama
#: pulls; the RAM figures are approximate and unmeasured.
LOCAL_PROFILES: tuple[ModelProfile, ...] = (
    ModelProfile(
        name="local-small",
        provider="ollama",
        model="qwen2.5-coder:1.5b",
        kind=ProviderKind.LOCAL,
        capabilities=_SQL | {C.LOCAL_EXECUTION, C.STREAMING},
        context_tokens=32768,
        max_output_tokens=1024,
        supports_native_json_schema=True,
        quality_hint=35,
        notes="CPU-friendly (~2 GB). Apache-2.0. Good enough for classification and short SQL.",
    ),
    ModelProfile(
        name="local-balanced",
        provider="ollama",
        model="qwen2.5-coder:7b",
        kind=ProviderKind.LOCAL,
        capabilities=_SQL | {C.LOCAL_EXECUTION, C.STREAMING, C.TOOL_CALLING, C.LONG_CONTEXT},
        context_tokens=32768,
        max_output_tokens=2048,
        supports_native_json_schema=True,
        quality_hint=65,
        notes="~6 GB. Apache-2.0. Slow but usable on CPU; comfortable on a small GPU.",
    ),
    ModelProfile(
        name="local-quality",
        provider="ollama",
        model="qwen2.5-coder:14b",
        kind=ProviderKind.LOCAL,
        capabilities=_SQL | {C.LOCAL_EXECUTION, C.STREAMING, C.TOOL_CALLING, C.LONG_CONTEXT},
        context_tokens=32768,
        max_output_tokens=4096,
        supports_native_json_schema=True,
        quality_hint=80,
        notes="~10 GB. Apache-2.0. GPU recommended.",
    ),
)

FAKE_PROFILE = ModelProfile(
    name="fake",
    provider="fake",
    model="fixture",
    kind=ProviderKind.FAKE,
    capabilities=frozenset(C) - {C.EMBEDDING, C.RERANK},
    supports_native_json_schema=True,
    quality_hint=1,
    notes="Deterministic fixtures. Used by CI and the end-to-end suite; never in production.",
)

#: Optional hosted profiles. Each is enabled only when its API key is present.
REMOTE_PROFILES: tuple[ModelProfile, ...] = (
    ModelProfile(
        name="gemini-flash",
        provider="gemini",
        model="gemini-2.5-flash",
        kind=ProviderKind.REMOTE,
        capabilities=_SQL | {C.TOOL_CALLING, C.LONG_CONTEXT, C.STREAMING},
        context_tokens=1_000_000,
        supports_native_json_schema=True,
        quality_hint=78,
    ),
    ModelProfile(
        name="groq-qwen",
        provider="groq",
        model="qwen/qwen3-32b",
        kind=ProviderKind.REMOTE,
        capabilities=_SQL | {C.TOOL_CALLING, C.STREAMING},
        context_tokens=131_072,
        quality_hint=72,
    ),
    ModelProfile(
        name="openai-gpt4o",
        provider="openai",
        model="gpt-4o",
        kind=ProviderKind.REMOTE,
        capabilities=_SQL | {C.TOOL_CALLING, C.LONG_CONTEXT, C.STREAMING},
        context_tokens=128_000,
        supports_native_json_schema=True,
        quality_hint=88,
    ),
    ModelProfile(
        name="anthropic-sonnet",
        provider="anthropic",
        model="claude-sonnet-4-5",
        kind=ProviderKind.REMOTE,
        capabilities=_SQL | {C.TOOL_CALLING, C.LONG_CONTEXT, C.STREAMING},
        context_tokens=200_000,
        quality_hint=90,
    ),
    ModelProfile(
        name="deepseek-chat",
        provider="deepseek",
        model="deepseek-chat",
        kind=ProviderKind.REMOTE,
        capabilities=_SQL | {C.TOOL_CALLING, C.LONG_CONTEXT},
        context_tokens=64_000,
        quality_hint=74,
    ),
    ModelProfile(
        name="openrouter",
        provider="openrouter",
        model="qwen/qwen-2.5-coder-32b-instruct",
        kind=ProviderKind.REMOTE,
        capabilities=_SQL | {C.TOOL_CALLING},
        context_tokens=32_768,
        quality_hint=70,
    ),
)

ALL_PROFILES: tuple[ModelProfile, ...] = (*LOCAL_PROFILES, FAKE_PROFILE, *REMOTE_PROFILES)

#: Which environment variable makes a remote provider usable.
PROVIDER_KEY_ENV: dict[str, str] = {
    "gemini": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def profile(name: str) -> ModelProfile:
    for candidate in ALL_PROFILES:
        if candidate.name == name:
            return candidate
    known = ", ".join(p.name for p in ALL_PROFILES)
    raise KeyError(f"Unknown model profile '{name}'. Known profiles: {known}")


def profiles_by_name() -> dict[str, ModelProfile]:
    return {p.name: p for p in ALL_PROFILES}


def registry_version() -> str:
    """A hash over every profile, recorded on runs so a config change is visible in the history."""
    payload = json.dumps(
        [
            {
                "name": p.name,
                "provider": p.provider,
                "model": p.model,
                "capabilities": sorted(c.value for c in p.capabilities),
                "temperature": p.temperature,
                "context": p.context_tokens,
            }
            for p in ALL_PROFILES
        ],
        sort_keys=True,
    )
    return "models@" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def remote_provider_available(provider_name: str, env: dict[str, str] | None = None) -> bool:
    environment = env if env is not None else os.environ
    key = PROVIDER_KEY_ENV.get(provider_name)
    return bool(key and environment.get(key))


@dataclass(frozen=True, slots=True)
class EnabledProfiles:
    profiles: tuple[ModelProfile, ...]
    reasons: dict[str, str]

    def names(self) -> list[str]:
        return [p.name for p in self.profiles]


def enabled_profiles(
    selection: str = "auto",
    *,
    allow_remote: bool = True,
    env: dict[str, str] | None = None,
) -> EnabledProfiles:
    """Which profiles this deployment may use, and why the others were excluded.

    ``selection`` is ``MODEL_PROFILE``: ``auto`` (everything usable), ``fake`` (CI), or an explicit
    profile name, which pins routing to that one profile.
    """
    environment = env if env is not None else dict(os.environ)
    chosen = (selection or "auto").strip().lower()
    reasons: dict[str, str] = {}

    if chosen == "fake":
        return EnabledProfiles(
            (FAKE_PROFILE,),
            {p.name: "MODEL_PROFILE=fake" for p in ALL_PROFILES if p.name != "fake"},
        )

    if chosen not in {"auto", ""}:
        pinned = profile(chosen)
        reasons = {p.name: f"MODEL_PROFILE={chosen}" for p in ALL_PROFILES if p.name != chosen}
        if pinned.kind is ProviderKind.REMOTE and not remote_provider_available(
            pinned.provider, environment
        ):
            key = PROVIDER_KEY_ENV.get(pinned.provider, "an API key")
            raise RuntimeError(
                f"MODEL_PROFILE={chosen} needs {key}, which is not set. "
                "Use a local profile (local-small / local-balanced / local-quality) or set the key."
            )
        return EnabledProfiles((pinned,), reasons)

    usable: list[ModelProfile] = list(LOCAL_PROFILES)
    reasons["fake"] = "the fake provider is only used when MODEL_PROFILE=fake"
    for candidate in REMOTE_PROFILES:
        if not allow_remote:
            reasons[candidate.name] = "remote providers are disabled by the egress policy"
        elif remote_provider_available(candidate.provider, environment):
            usable.append(candidate)
        else:
            reasons[candidate.name] = (
                f"{PROVIDER_KEY_ENV.get(candidate.provider, 'API key')} is not set"
            )
    return EnabledProfiles(tuple(usable), reasons)


__all__ = [
    "ALL_PROFILES",
    "FAKE_PROFILE",
    "LOCAL_PROFILES",
    "PROVIDER_KEY_ENV",
    "REMOTE_PROFILES",
    "EnabledProfiles",
    "enabled_profiles",
    "profile",
    "profiles_by_name",
    "registry_version",
    "remote_provider_available",
]
