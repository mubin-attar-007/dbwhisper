"""The typed application-mode policy: one object per mode, no scattered mode checks."""

from __future__ import annotations

import pytest

from app.platform.modes import (
    AppMode,
    EgressPolicy,
    NetworkPolicyLevel,
    all_policies,
    policy_for,
    resolve_mode,
)


class TestResolveMode:
    def test_explicit_mode_wins(self):
        assert resolve_mode("demo", "production") is AppMode.DEMO
        assert resolve_mode("PRODUCTION", "development") is AppMode.PRODUCTION
        assert resolve_mode("self-hosted") is AppMode.SELF_HOSTED

    def test_legacy_app_env_production_maps_to_production(self, caplog):
        with caplog.at_level("WARNING"):
            assert resolve_mode(None, "production") is AppMode.PRODUCTION
            assert resolve_mode("", "prod") is AppMode.PRODUCTION
        assert "inferring APP_MODE" in caplog.text

    def test_default_is_self_hosted(self):
        assert resolve_mode(None, None) is AppMode.SELF_HOSTED
        assert resolve_mode(None, "development") is AppMode.SELF_HOSTED
        assert resolve_mode(None, "test") is AppMode.SELF_HOSTED

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="Unsupported APP_MODE"):
            resolve_mode("staging")


class TestPolicies:
    def test_every_mode_has_a_policy(self):
        assert {p.mode for p in all_policies()} == set(AppMode)
        for mode in AppMode:
            assert policy_for(mode).mode is mode
            assert policy_for(mode.value).mode is mode

    def test_policies_are_immutable(self):
        with pytest.raises((AttributeError, TypeError)):
            policy_for(AppMode.DEMO).auth_required = True  # type: ignore[misc]

    def test_demo_is_locked_down(self):
        p = policy_for(AppMode.DEMO)
        assert p.allow_arbitrary_connections is False
        assert p.network_policy is NetworkPolicyLevel.BUNDLED_ONLY
        assert p.allow_registration is False
        assert p.debug_endpoints is False
        assert p.rate_limit_profile == "strict"
        assert p.trace_sanitization == "full"
        assert p.demo_reset_enabled is True
        assert p.persist_sensitive_run_data is False
        assert p.allow_anonymous_query is True

    def test_production_is_strict(self):
        p = policy_for(AppMode.PRODUCTION)
        assert p.auth_required is True
        assert p.allow_anonymous_query is False
        assert p.network_policy is NetworkPolicyLevel.PUBLIC_STRICT
        assert p.refuse_writable_connections is True
        assert p.allow_writable_override is True  # admin override, audited
        assert p.secret_encryption_required is True
        assert p.cookie_secure is True
        assert p.cors_wildcard_allowed is False
        assert p.debug_endpoints is False
        assert p.demo_reset_enabled is False

    def test_self_hosted_is_permissive_but_encrypts(self):
        p = policy_for(AppMode.SELF_HOSTED)
        assert p.allow_arbitrary_connections is True
        assert p.network_policy is NetworkPolicyLevel.PRIVATE_ALLOWED
        assert p.secret_encryption_required is True
        assert p.default_egress_policy is EgressPolicy.LOCAL_ONLY
        assert p.auth_required is False

    def test_only_self_hosted_allows_wildcard_cors_and_debug(self):
        for p in all_policies():
            assert p.cors_wildcard_allowed is (p.mode is AppMode.SELF_HOSTED)
            assert p.debug_endpoints is (p.mode is AppMode.SELF_HOSTED)


class TestEgressPolicy:
    def test_ordering(self):
        ranks = [p.rank for p in EgressPolicy]
        assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)

    def test_permits(self):
        assert EgressPolicy.REMOTE_ALLOWED.permits(EgressPolicy.LOCAL_ONLY)
        assert EgressPolicy.REMOTE_ALLOWED.permits(EgressPolicy.AGGREGATES_REMOTE)
        assert not EgressPolicy.LOCAL_ONLY.permits(EgressPolicy.SCHEMA_ONLY_REMOTE)
        assert EgressPolicy.SCHEMA_ONLY_REMOTE.permits(EgressPolicy.SCHEMA_ONLY_REMOTE)
