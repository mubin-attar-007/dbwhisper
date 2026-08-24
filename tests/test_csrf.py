"""CSRF: the decision table, and the FastAPI dependency that applies it.

Most of these exercise :func:`check_request` directly, because that is where the decision lives and
a pure function lets each row of the table be asserted on its own rather than through an HTTP stack.
The last section drives a real request through the dependency to prove the adapter wires the same
decision to a 403.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.platform.audit import AuditAction, InMemoryAuditSink, audit_to
from app.platform.modes import AppMode
from app.security.csrf import (
    COOKIE_NAME,
    HEADER_NAME,
    CsrfPolicy,
    CsrfStatus,
    check_request,
    clear_csrf_cookie,
    issue_token,
    policy_for_settings,
    require_csrf,
    set_csrf_cookie,
    tokens_match,
)

APP_ORIGIN = "https://app.example.com"
EVIL_ORIGIN = "https://evil.example.net"
TOKEN = "a" * 32
OTHER_TOKEN = "b" * 32


def enforced_policy(**overrides) -> CsrfPolicy:
    base = {
        "mode": AppMode.PRODUCTION,
        "enforced": True,
        "check_origin": True,
        "allowed_origins": (APP_ORIGIN,),
        "rationale": "test",
    }
    base.update(overrides)
    return CsrfPolicy(**base)


def post(policy: CsrfPolicy | None = None, **overrides):
    kwargs = {
        "method": "POST",
        "policy": policy or enforced_policy(),
        "origin": APP_ORIGIN,
        "cookie_token": TOKEN,
        "header_token": TOKEN,
        "has_session": True,
    }
    kwargs.update(overrides)
    return check_request(**kwargs)


# ---------------------------------------------------------------------------------------------
# The four cases the brief names
# ---------------------------------------------------------------------------------------------


def test_valid_token_is_accepted():
    result = post()
    assert result.ok
    assert result.status is CsrfStatus.OK
    assert result.checks_run == ("origin", "token")


@pytest.mark.parametrize("missing", ["cookie_token", "header_token"])
def test_missing_token_is_rejected(missing):
    result = post(**{missing: None})
    assert not result.ok
    assert result.status is CsrfStatus.TOKEN_MISSING


def test_mismatched_token_is_rejected():
    result = post(header_token=OTHER_TOKEN)
    assert not result.ok
    assert result.status is CsrfStatus.TOKEN_MISMATCH


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "TRACE", "get", "options"])
def test_safe_methods_are_exempt(method):
    result = check_request(method=method, policy=enforced_policy(), cookie_token=None)
    assert result.ok
    assert result.status is CsrfStatus.SAFE_METHOD


def test_cross_origin_request_is_rejected_even_with_a_matching_token_pair():
    result = post(origin=EVIL_ORIGIN)
    assert not result.ok
    assert result.status is CsrfStatus.ORIGIN_REJECTED
    # Origin is checked first, so the token comparison never runs on a request we already refuse.
    assert result.checks_run == ("origin",)


# ---------------------------------------------------------------------------------------------
# Unsafe methods beyond POST
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_state_changing_method_is_checked(method):
    assert post(method=method, header_token=OTHER_TOKEN).status is CsrfStatus.TOKEN_MISMATCH


# ---------------------------------------------------------------------------------------------
# Origin and Referer handling
# ---------------------------------------------------------------------------------------------


def test_referer_is_used_when_origin_is_absent():
    assert post(origin=None, referer=f"{APP_ORIGIN}/dashboard?tab=1").ok


def test_a_referer_from_another_site_is_rejected():
    result = post(origin=None, referer=f"{EVIL_ORIGIN}/attack.html")
    assert result.status is CsrfStatus.ORIGIN_REJECTED


def test_a_session_bearing_request_with_no_origin_or_referer_is_refused():
    # It cannot be distinguished from a forgery, so it fails closed. Non-browser clients are
    # exempted earlier by the no-session rule instead.
    result = post(origin=None, referer=None)
    assert result.status is CsrfStatus.ORIGIN_MISSING


def test_the_null_origin_never_satisfies_the_allowlist():
    assert post(origin="null").status is CsrfStatus.ORIGIN_REJECTED


def test_origin_matching_ignores_case_and_trailing_path():
    assert post(origin="HTTPS://APP.EXAMPLE.COM").ok
    assert post(origin=f"{APP_ORIGIN}/some/path").ok


def test_a_different_port_is_a_different_origin():
    assert post(origin="https://app.example.com:8443").status is CsrfStatus.ORIGIN_REJECTED


def test_a_different_scheme_is_a_different_origin():
    assert post(origin="http://app.example.com").status is CsrfStatus.ORIGIN_REJECTED


def test_the_requests_own_origin_is_accepted_when_self_origin_is_trusted():
    policy = enforced_policy(allowed_origins=("https://other.example.com",))
    assert post(policy=policy, origin=APP_ORIGIN, self_origin=APP_ORIGIN).ok


def test_self_origin_can_be_switched_off_for_deployments_behind_an_untrusted_host_header():
    policy = enforced_policy(
        allowed_origins=("https://other.example.com",), trust_self_origin=False
    )
    result = post(policy=policy, origin=APP_ORIGIN, self_origin=APP_ORIGIN)
    assert result.status is CsrfStatus.ORIGIN_REJECTED


def test_the_token_is_still_required_when_the_origin_check_is_disabled():
    # With CORS_ALLOW_ORIGINS=* there is no allowlist to compare against, so the double-submit
    # token is the whole control and must not be skipped along with the origin check.
    policy = enforced_policy(check_origin=False, allowed_origins=())
    assert post(policy=policy, origin=EVIL_ORIGIN, header_token=OTHER_TOKEN).status is (
        CsrfStatus.TOKEN_MISMATCH
    )
    assert post(policy=policy, origin=EVIL_ORIGIN).ok


# ---------------------------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------------------------


def test_a_request_without_a_session_is_exempt_because_it_has_nothing_to_borrow():
    result = post(has_session=False, cookie_token=None, header_token=None)
    assert result.ok
    assert result.status is CsrfStatus.NO_AMBIENT_AUTHORITY
    assert result.checks_run == ()


def test_the_default_assumes_a_session_so_an_unsure_caller_gets_the_strict_path():
    result = check_request(method="POST", policy=enforced_policy(), origin=APP_ORIGIN)
    assert not result.ok


def test_an_unenforced_policy_allows_and_says_why():
    policy = enforced_policy(enforced=False, rationale="Self-hosted: opt-in and currently off.")
    result = post(policy=policy, cookie_token=None, header_token=None)
    assert result.ok
    assert result.status is CsrfStatus.NOT_ENFORCED
    assert "opt-in" in result.detail


# ---------------------------------------------------------------------------------------------
# Token primitives
# ---------------------------------------------------------------------------------------------


def test_issued_tokens_are_random_and_long_enough():
    tokens = {issue_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


def test_two_empty_tokens_do_not_match():
    assert not tokens_match("", "")
    assert not tokens_match(None, None)


def test_a_short_token_is_rejected_even_when_both_sides_agree():
    assert not tokens_match("short", "short")
    assert post(cookie_token="short", header_token="short").status is CsrfStatus.TOKEN_TOO_SHORT


def test_matching_tokens_match():
    assert tokens_match(TOKEN, TOKEN)


# ---------------------------------------------------------------------------------------------
# Policy resolution per mode
# ---------------------------------------------------------------------------------------------


def settings_for(mode: str, origins: str = APP_ORIGIN, **kwargs) -> Settings:
    return Settings(app_mode=mode, cors_allow_origins=origins, **kwargs)


def test_production_enforces_by_default():
    policy = policy_for_settings(settings_for("production"))
    assert policy.enforced and policy.check_origin
    assert policy.allowed_origins == (APP_ORIGIN,)


def test_self_hosted_is_off_by_default_but_still_applicable():
    policy = policy_for_settings(settings_for("self_hosted"))
    assert not policy.enforced
    assert policy.applicable  # it has sessions; it has simply not turned the control on


def test_self_hosted_can_opt_in(monkeypatch):
    monkeypatch.setenv("DBW_CSRF_ENFORCED", "1")
    assert policy_for_settings(settings_for("self_hosted")).enforced


def test_demo_is_not_applicable_rather_than_merely_disabled():
    policy = policy_for_settings(settings_for("demo"))
    assert not policy.enforced
    assert not policy.applicable
    assert "no login" in policy.rationale


def test_a_wildcard_cors_origin_disables_the_origin_half_and_says_so():
    policy = policy_for_settings(settings_for("production", origins="*"))
    assert policy.enforced
    assert not policy.check_origin
    assert policy.allowed_origins == ()


# ---------------------------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------------------------


def test_the_csrf_cookie_is_readable_by_script_which_is_the_point_of_double_submit():
    app = FastAPI()

    @app.get("/issue")
    def issue(response_marker: bool = True):
        from fastapi.responses import JSONResponse

        response = JSONResponse({"ok": response_marker})
        set_csrf_cookie(response, TOKEN, secure=False)
        return response

    client = TestClient(app)
    raw = client.get("/issue").headers["set-cookie"]
    assert f"{COOKIE_NAME}={TOKEN}" in raw
    assert "HttpOnly" not in raw  # the front end has to read it to echo it back
    assert "samesite=lax" in raw.lower()


def test_clearing_the_cookie_expires_it():
    app = FastAPI()

    @app.get("/clear")
    def clear():
        from fastapi.responses import JSONResponse

        response = JSONResponse({})
        clear_csrf_cookie(response)
        return response

    raw = TestClient(app).get("/clear").headers["set-cookie"]
    assert COOKIE_NAME in raw
    assert "Max-Age=0" in raw or "expires=" in raw.lower()


# ---------------------------------------------------------------------------------------------
# The dependency, end to end
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def guarded_client(monkeypatch):
    """A tiny app whose one mutating route is behind ``require_csrf``, in production mode."""
    monkeypatch.setenv("APP_MODE", "production")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", APP_ORIGIN)
    from app.core import config

    config.get_settings.cache_clear()

    app = FastAPI()

    @app.post("/mutate", dependencies=[Depends(require_csrf)])
    def mutate():
        return {"ok": True}

    try:
        yield TestClient(app)
    finally:
        config.get_settings.cache_clear()


def _session_cookies(client, *, csrf: str | None = TOKEN) -> None:
    client.cookies.set("dbw_session", "session-value")
    if csrf is not None:
        client.cookies.set(COOKIE_NAME, csrf)


def test_dependency_allows_a_correctly_formed_request(guarded_client):
    _session_cookies(guarded_client)
    response = guarded_client.post("/mutate", headers={"Origin": APP_ORIGIN, HEADER_NAME: TOKEN})
    assert response.status_code == 200


def test_dependency_rejects_a_missing_header(guarded_client):
    _session_cookies(guarded_client)
    response = guarded_client.post("/mutate", headers={"Origin": APP_ORIGIN})
    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "token_missing"


def test_dependency_rejects_a_cross_origin_request(guarded_client):
    _session_cookies(guarded_client)
    response = guarded_client.post("/mutate", headers={"Origin": EVIL_ORIGIN, HEADER_NAME: TOKEN})
    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "origin_rejected"


def test_dependency_lets_an_api_client_with_no_session_cookie_through(guarded_client):
    assert guarded_client.post("/mutate").status_code == 200


def test_a_rejection_is_audited(guarded_client):
    _session_cookies(guarded_client, csrf=None)
    sink = InMemoryAuditSink()
    with audit_to(sink):
        guarded_client.post("/mutate", headers={"Origin": APP_ORIGIN})
    events = sink.of(AuditAction.CSRF_REJECTED)
    assert len(events) == 1
    assert events[0].subject == "/mutate"
    assert events[0].reason == "token_missing"
