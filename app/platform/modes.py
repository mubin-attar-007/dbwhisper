"""Application modes and the single typed policy object derived from them.

DBWhisper runs in exactly one of three modes, selected with ``APP_MODE``:

* ``demo``        - public demonstration: bundled data sources only, anonymous access, strict limits.
* ``self_hosted`` - local / private-network use: full connection management, local models.
* ``production``  - multi-tenant deployment: mandatory auth, encrypted secrets, strict network policy.

Every behavioural difference between modes lives in :class:`AppModePolicy`. Code elsewhere asks the
policy object (``policy.allow_arbitrary_connections``) instead of checking the mode name, so a reviewer
can read this one file and know what each mode permits.

``APP_ENV`` (``development`` / ``production``) is *not* the same thing: it drives logging format and
cookie defaults. For backward compatibility ``APP_ENV=production`` without an explicit ``APP_MODE``
resolves to :attr:`AppMode.PRODUCTION` and a warning is logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

logger = logging.getLogger(__name__)


class AppMode(StrEnum):
    DEMO = "demo"
    SELF_HOSTED = "self_hosted"
    PRODUCTION = "production"


class EgressPolicy(StrEnum):
    """What may leave the deployment towards a *remote* model provider.

    Ordered from most to least restrictive. Local providers (Ollama, fastembed, fake) are never
    subject to egress policy because nothing leaves the host.
    """

    LOCAL_ONLY = "LOCAL_ONLY"  # no remote provider may be used at all
    SCHEMA_ONLY_REMOTE = "SCHEMA_ONLY_REMOTE"  # table/column names + approved descriptions only
    MASKED_METADATA_REMOTE = "MASKED_METADATA_REMOTE"  # schema + masked sample values
    AGGREGATES_REMOTE = "AGGREGATES_REMOTE"  # schema + deterministic aggregates (no row samples)
    REMOTE_ALLOWED = "REMOTE_ALLOWED"  # schema + policy-approved result sample

    @property
    def rank(self) -> int:
        return list(EgressPolicy).index(self)

    def permits(self, required: EgressPolicy) -> bool:
        """True when this (active) policy is at least as permissive as ``required``."""
        return self.rank >= required.rank


class NetworkPolicyLevel(StrEnum):
    BUNDLED_ONLY = "bundled_only"  # only data sources shipped with the app (demo)
    PRIVATE_ALLOWED = "private_allowed"  # loopback / RFC1918 permitted (self-hosted default)
    PUBLIC_STRICT = "public_strict"  # deny loopback, link-local, metadata; optional allowlist


RateLimitProfile = Literal["strict", "relaxed", "configurable"]
TraceSanitization = Literal["full", "standard"]


@dataclass(frozen=True, slots=True)
class AppModePolicy:
    """Immutable description of what a mode permits. Construct via :func:`policy_for`."""

    mode: AppMode
    auth_required: bool
    allow_anonymous_query: bool
    allow_registration: bool
    allow_arbitrary_connections: bool
    network_policy: NetworkPolicyLevel
    refuse_writable_connections: bool
    allow_writable_override: bool
    secret_encryption_required: bool
    cookie_secure: bool
    cors_wildcard_allowed: bool
    debug_endpoints: bool
    rate_limit_profile: RateLimitProfile
    trace_sanitization: TraceSanitization
    default_egress_policy: EgressPolicy
    allow_remote_providers: bool
    demo_reset_enabled: bool
    persist_sensitive_run_data: bool

    @property
    def is_demo(self) -> bool:
        return self.mode is AppMode.DEMO

    @property
    def is_production(self) -> bool:
        return self.mode is AppMode.PRODUCTION


_POLICIES: dict[AppMode, AppModePolicy] = {
    AppMode.DEMO: AppModePolicy(
        mode=AppMode.DEMO,
        auth_required=False,
        allow_anonymous_query=True,
        allow_registration=False,
        allow_arbitrary_connections=False,
        network_policy=NetworkPolicyLevel.BUNDLED_ONLY,
        refuse_writable_connections=True,
        allow_writable_override=False,
        secret_encryption_required=False,
        cookie_secure=True,
        cors_wildcard_allowed=False,
        debug_endpoints=False,
        rate_limit_profile="strict",
        trace_sanitization="full",
        default_egress_policy=EgressPolicy.SCHEMA_ONLY_REMOTE,
        allow_remote_providers=True,
        demo_reset_enabled=True,
        persist_sensitive_run_data=False,
    ),
    AppMode.SELF_HOSTED: AppModePolicy(
        mode=AppMode.SELF_HOSTED,
        auth_required=False,
        allow_anonymous_query=True,
        allow_registration=True,
        allow_arbitrary_connections=True,
        network_policy=NetworkPolicyLevel.PRIVATE_ALLOWED,
        refuse_writable_connections=False,
        allow_writable_override=True,
        secret_encryption_required=True,
        cookie_secure=False,
        cors_wildcard_allowed=True,
        debug_endpoints=True,
        rate_limit_profile="relaxed",
        trace_sanitization="standard",
        default_egress_policy=EgressPolicy.LOCAL_ONLY,
        allow_remote_providers=True,
        demo_reset_enabled=False,
        persist_sensitive_run_data=True,
    ),
    AppMode.PRODUCTION: AppModePolicy(
        mode=AppMode.PRODUCTION,
        auth_required=True,
        allow_anonymous_query=False,
        allow_registration=True,
        allow_arbitrary_connections=True,
        network_policy=NetworkPolicyLevel.PUBLIC_STRICT,
        refuse_writable_connections=True,
        allow_writable_override=True,
        secret_encryption_required=True,
        cookie_secure=True,
        cors_wildcard_allowed=False,
        debug_endpoints=False,
        rate_limit_profile="configurable",
        trace_sanitization="full",
        default_egress_policy=EgressPolicy.SCHEMA_ONLY_REMOTE,
        allow_remote_providers=True,
        demo_reset_enabled=False,
        persist_sensitive_run_data=True,
    ),
}

_LEGACY_ENV_TO_MODE = {
    "production": AppMode.PRODUCTION,
    "prod": AppMode.PRODUCTION,
}


def resolve_mode(app_mode: str | None, app_env: str | None = None) -> AppMode:
    """Resolve the effective mode from ``APP_MODE`` with an ``APP_ENV`` fallback.

    * explicit ``APP_MODE`` wins (case-insensitive, ``self-hosted`` accepted);
    * else ``APP_ENV=production`` maps to production (logged once as a deprecation);
    * else ``self_hosted`` - the safe default for someone running the project locally.
    """
    raw = (app_mode or "").strip().lower().replace("-", "_")
    if raw:
        try:
            return AppMode(raw)
        except ValueError as exc:
            valid = ", ".join(m.value for m in AppMode)
            raise ValueError(f"Unsupported APP_MODE '{app_mode}'. Use one of: {valid}") from exc
    env = (app_env or "").strip().lower()
    if env in _LEGACY_ENV_TO_MODE:
        logger.warning(
            "APP_MODE is not set; inferring APP_MODE=%s from APP_ENV=%s. "
            "Set APP_MODE explicitly (demo | self_hosted | production).",
            _LEGACY_ENV_TO_MODE[env].value,
            env,
        )
        return _LEGACY_ENV_TO_MODE[env]
    return AppMode.SELF_HOSTED


def policy_for(mode: AppMode | str) -> AppModePolicy:
    """Return the immutable policy for a mode."""
    if not isinstance(mode, AppMode):
        mode = AppMode(str(mode).strip().lower().replace("-", "_"))
    return _POLICIES[mode]


def all_policies() -> tuple[AppModePolicy, ...]:
    return tuple(_POLICIES[m] for m in AppMode)


__all__ = [
    "AppMode",
    "AppModePolicy",
    "EgressPolicy",
    "NetworkPolicyLevel",
    "all_policies",
    "policy_for",
    "resolve_mode",
]
