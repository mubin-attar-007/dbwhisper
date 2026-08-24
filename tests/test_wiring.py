"""The seams between the platform modules and the running application.

Each of these modules is tested thoroughly on its own; what is easy to get wrong — and what nothing
else would catch — is forgetting to *mount* one. A CSRF module with no route depending on it, or a
metrics registry with no endpoint serving it, passes its own tests perfectly while protecting and
measuring nothing.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.main import app
from app.security.csrf import COOKIE_NAME as CSRF_COOKIE

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

#: Routes that change state but must stay reachable without a session — they establish one, or are
#: unauthenticated by design.
#: Only the two routes that run *before* a session exists. A stale CSRF cookie must not be able
#: to lock someone out of logging in, and neither route has ambient authority to abuse.
CSRF_EXEMPT = {"/auth/register", "/auth/login"}


def _state_changing_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and (set(route.methods or set()) - SAFE_METHODS)
    ]


def _dependency_names(route: APIRoute) -> set[str]:
    return {
        getattr(d.call, "__name__", "") for d in route.dependant.dependencies if d.call is not None
    }


class TestCsrfIsActuallyMounted:
    def test_there_are_state_changing_routes_to_protect(self):
        assert _state_changing_routes(), "the sweep below would be vacuous"

    @pytest.mark.parametrize(
        "route",
        [
            pytest.param(r, id=f"{sorted(r.methods)[0]} {r.path}")
            for r in _state_changing_routes()
            if r.path not in CSRF_EXEMPT
        ],
    )
    def test_every_state_changing_route_depends_on_the_csrf_check(self, route):
        assert "require_csrf" in _dependency_names(route), (
            f"{route.path} changes state but has no CSRF dependency; a browser session cookie "
            "would be usable cross-site against it"
        )

    def test_safe_routes_are_not_burdened_with_it(self):
        """A GET carrying the dependency is harmless but misleading in the generated docs."""
        offenders = [
            route.path
            for route in app.routes
            if isinstance(route, APIRoute)
            and not (set(route.methods or set()) - SAFE_METHODS)
            and "require_csrf" in _dependency_names(route)
        ]
        assert offenders == []


class TestSessionAndTokenTravelTogether:
    """A session cookie without a CSRF cookie is a session that can never pass the check."""

    def test_logging_in_issues_both_cookies(self, client, monkeypatch):
        import app.api.auth as auth_module

        monkeypatch.setattr(auth_module, "get_session", lambda _url: _FakeSession())
        monkeypatch.setattr(auth_module, "verify_password", lambda *_a: True)
        monkeypatch.setattr(auth_module, "needs_rehash", lambda _h: False)
        monkeypatch.setattr(auth_module, "create_session", lambda *_a, **_k: "session-token")

        response = client.post(
            "/auth/login", json={"email": "a@example.com", "password": "hunter2hunter2"}
        )
        assert response.status_code == 200, response.text
        assert CSRF_COOKIE in response.cookies, "a session was issued with no CSRF token"

    def test_the_csrf_cookie_is_readable_by_script(self, client, monkeypatch):
        """Double-submit only works if the front end can read the value back to echo it."""
        import app.api.auth as auth_module

        monkeypatch.setattr(auth_module, "get_session", lambda _url: _FakeSession())
        monkeypatch.setattr(auth_module, "verify_password", lambda *_a: True)
        monkeypatch.setattr(auth_module, "needs_rehash", lambda _h: False)
        monkeypatch.setattr(auth_module, "create_session", lambda *_a, **_k: "session-token")

        response = client.post(
            "/auth/login", json={"email": "a@example.com", "password": "hunter2hunter2"}
        )
        header = next(
            v for v in response.headers.get_list("set-cookie") if v.startswith(f"{CSRF_COOKIE}=")
        )
        assert "httponly" not in header.lower(), "the client must be able to read it"


class TestMetricsEndpoint:
    def test_it_serves_the_prometheus_exposition_format(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "dbwhisper" in response.text or "python_info" in response.text

    def test_it_is_absent_rather_than_empty_when_disabled(self, client, monkeypatch):
        import app.main as main_module
        from app.core.config import get_settings

        disabled = get_settings().model_copy(update={"metrics_enabled": False})
        monkeypatch.setattr(main_module, "get_settings", lambda: disabled)
        assert client.get("/metrics").status_code == 404

    def test_it_is_not_in_the_public_schema(self, client):
        assert "/metrics" not in client.get("/openapi.json").json()["paths"]

    def test_it_exposes_no_question_row_or_credential(self, client):
        body = client.get("/metrics").text.lower()
        for leak in ("password", "postgresql://", "select ", "connection_string"):
            assert leak not in body, f"{leak!r} reached the metrics endpoint"


class _FakeUser:
    id = 1
    email = "a@example.com"
    password_hash = "x"
    is_active = True
    created_at = None
    is_admin = False


class _FakeQuery:
    def filter(self, *_a):
        return self

    def first(self):
        return _FakeUser()


class _FakeSession:
    def query(self, *_a):
        return _FakeQuery()

    def commit(self):
        pass

    def close(self):
        pass

    def refresh(self, *_a):
        pass


class TestAuditEventsActuallyFire:
    """The audit module records what it is told to. These prove something tells it."""

    def test_a_policy_denial_is_recorded_without_the_sql(self):
        from app.execution.service import ExecutionRequest, execute
        from app.platform.audit import AuditAction, AuditOutcome, InMemoryAuditSink, audit_to

        sink = InMemoryAuditSink()
        with audit_to(sink):
            result = execute(
                ExecutionRequest(
                    sql="DROP TABLE customers",
                    connection_string="sqlite:///:memory:",
                    db_flag="demo",
                )
            )

        assert result.success is False
        denials = [e for e in sink.events if e.action is AuditAction.POLICY_DENIED]
        assert len(denials) == 1, "a refused statement must leave a record"

        event = denials[0]
        assert event.outcome is AuditOutcome.DENIED
        assert event.subject == "demo"
        assert event.detail["statement_type"] == "Drop"
        assert event.detail["policy_version"].startswith("sql_policy@")
        assert event.detail["rules"], "the rules that fired are what makes a denial reviewable"
        assert "DROP TABLE" not in str(event.detail), "the statement itself does not belong here"
        assert "customers" not in str(event.detail), "nor the identifiers it named"

    def test_an_allowed_statement_records_no_denial(self):
        from app.execution.service import ExecutionRequest, execute
        from app.platform.audit import AuditAction, InMemoryAuditSink, audit_to

        sink = InMemoryAuditSink()
        with audit_to(sink):
            execute(
                ExecutionRequest(
                    sql="SELECT 1",
                    connection_string="sqlite:///:memory:",
                    db_flag="demo",
                )
            )
        assert [e for e in sink.events if e.action is AuditAction.POLICY_DENIED] == []

    def test_a_tenant_denial_is_recorded(self, monkeypatch):
        import app.main as main_module
        from app.platform.audit import AuditAction, AuditOutcome, InMemoryAuditSink, audit_to

        settings = main_module.get_settings().model_copy(update={"user_auth_enabled": True})
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)
        monkeypatch.setattr(
            "app.security.user_auth.get_current_user", lambda _r: None, raising=False
        )
        monkeypatch.setattr(
            "app.security.tenancy.user_can_access_db_flag", lambda *_a: False, raising=False
        )

        sink = InMemoryAuditSink()
        with audit_to(sink), pytest.raises(HTTPException) as exc_info:
            main_module._enforce_db_access(_FakeRequest(), "someone_elses_db")

        assert exc_info.value.status_code == 403
        denials = [e for e in sink.events if e.action is AuditAction.TENANT_ACCESS_DENIED]
        assert len(denials) == 1
        assert denials[0].subject == "someone_elses_db"
        assert denials[0].outcome is AuditOutcome.DENIED


class _FakeClient:
    host = "203.0.113.7"


class _FakeRequest:
    client = _FakeClient()
    cookies: ClassVar[dict] = {}
    headers: ClassVar[dict] = {}
