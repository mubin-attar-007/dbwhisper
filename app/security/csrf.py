"""CSRF defence for the cookie-session endpoints.

Why this exists at all. ``app/security/user_auth.py`` sets an ``httponly`` session cookie with
``samesite="lax"``, and ``app/main.py`` enables ``allow_credentials`` on CORS whenever an explicit
origin allowlist is configured. ``SameSite=Lax`` stops a cross-site *form* POST, which is the common
case, but it is a single layer and it has known edges: it does not constrain a same-site subdomain,
some embedded webviews and older clients relax or ignore it, and a top-level GET navigation still
carries the cookie. The audit recorded this as the only CSRF control in the system
(``docs/v2/CURRENT_STATE_AUDIT.md`` §10.7). This module is the second layer.

The design is the boring, well-understood one, in this order:

1. **Safe methods are exempt.** GET/HEAD/OPTIONS/TRACE are assumed side-effect free. If a GET route
   ever mutates state, that is the bug to fix, not this list to extend.
2. **Requests with no ambient authority are exempt.** CSRF is an attack on *ambient* credentials -
   a cookie the browser attaches on the attacker's behalf. A request carrying no session cookie has
   nothing to abuse, and an API-key client (curl, CI, the eval harness) sends no cookie. Treating
   those as CSRF failures would break every non-browser caller while protecting nothing.
3. **Origin, then token.** ``Origin`` (falling back to the ``Referer``'s origin) must be one the
   deployment recognises. Then a double-submit token: a random value is set in a readable cookie and
   must be echoed in the ``X-CSRF-Token`` header, compared with :func:`hmac.compare_digest`. An
   attacker's page can make the browser *send* our cookie but cannot *read* it, so it cannot
   populate the header.

Origin is checked before the token because a cross-origin request is a decision we can make from
one header, and refusing it early keeps the token comparison off the path for requests we already
know we will not honour.

**Where it is enforced** is a mode decision, not a per-route one - see :func:`policy_for_settings`.
Production enforces. Demo has no login, therefore no ambient authority, therefore nothing to defend;
it is marked not-applicable rather than "off", because those are different statements. Self-hosted
is configurable and defaults to off, since a single-user local deployment usually runs with
``CORS_ALLOW_ORIGINS=*``, where a meaningful origin allowlist does not exist.

**Known limit, stated plainly.** When ``trust_self_origin`` is enabled the target origin is derived
from the request's own ``Host`` header, which a client controls unless a proxy normalises it. That
is the same trusted-proxy assumption the rate limiter makes (``TRUST_PROXY_HEADERS``, inert today -
audit §17.7). Configuring ``CORS_ALLOW_ORIGINS`` explicitly removes the dependency, which is why
production is expected to pin it.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status

from app.core.config import Settings, get_settings
from app.platform.audit import AuditAction, AuditActor, AuditOutcome, record
from app.platform.modes import AppMode

COOKIE_NAME = "dbw_csrf"
HEADER_NAME = "X-CSRF-Token"
TOKEN_BYTES = 32
MIN_TOKEN_LENGTH = 16

# Methods assumed to have no side effects. RFC 9110 calls these safe; the app is expected to keep
# them that way rather than this set growing.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Env var read directly rather than through Settings: app/core/config.py belongs to another track,
# so this module cannot add a field to it. The lead's wiring note proposes `csrf_enforced` on
# Settings; `policy_for_settings` already prefers that attribute when it exists.
ENFORCE_ENV_VAR = "DBW_CSRF_ENFORCED"


class CsrfStatus(StrEnum):
    """Why a request was allowed or refused. Carried into the audit event and the 403 body."""

    OK = "ok"
    SAFE_METHOD = "safe_method"
    NOT_ENFORCED = "not_enforced"
    NO_AMBIENT_AUTHORITY = "no_ambient_authority"
    ORIGIN_MISSING = "origin_missing"
    ORIGIN_REJECTED = "origin_rejected"
    TOKEN_MISSING = "token_missing"
    TOKEN_MISMATCH = "token_mismatch"
    TOKEN_TOO_SHORT = "token_too_short"

    @property
    def allowed(self) -> bool:
        return self in _ALLOWED_STATUSES


_ALLOWED_STATUSES = frozenset(
    {
        CsrfStatus.OK,
        CsrfStatus.SAFE_METHOD,
        CsrfStatus.NOT_ENFORCED,
        CsrfStatus.NO_AMBIENT_AUTHORITY,
    }
)


@dataclass(frozen=True, slots=True)
class CsrfPolicy:
    """What CSRF defence is active, and why. One instance per resolved application mode.

    ``enforced`` is the master switch. ``check_origin`` is separately false when the deployment has
    no meaningful origin allowlist (``CORS_ALLOW_ORIGINS=*``): comparing against "anything" is not a
    check, and pretending otherwise would be the kind of claim this project's claim audit exists to
    prevent. ``rationale`` is carried so an operator reading a 403 or a status endpoint can see the
    reasoning rather than guessing at a boolean.
    """

    mode: AppMode
    enforced: bool
    check_origin: bool
    allowed_origins: tuple[str, ...]
    rationale: str
    cookie_name: str = COOKIE_NAME
    header_name: str = HEADER_NAME
    trust_self_origin: bool = True
    cookie_secure: bool = True

    @property
    def applicable(self) -> bool:
        """Whether this mode has cookie sessions at all - i.e. whether CSRF is even a question.

        Distinct from :attr:`enforced`: self-hosted with enforcement off is a deployment that chose
        not to enable a control, while demo has nothing for the control to protect.
        """
        return self.mode is not AppMode.DEMO


@dataclass(frozen=True, slots=True)
class CsrfResult:
    """The outcome of one check, plus which checks actually ran.

    ``checks_run`` matters: "allowed" after two checks and "allowed" after zero checks are very
    different facts, and only one of them is a control.
    """

    status: CsrfStatus
    detail: str
    checks_run: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status.allowed


def _normalize_origin(value: str | None) -> str | None:
    """Reduce a URL or origin to ``scheme://host[:port]``, lower-cased. ``None`` when unusable.

    The literal ``"null"`` origin - sent by sandboxed iframes, ``data:`` documents and some
    privacy tools - is deliberately not normalised to anything matchable, so it can never satisfy
    an allowlist entry.
    """
    if not value:
        return None
    raw = value.strip()
    if not raw or raw.lower() == "null":
        return None
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _origins_from(settings: Settings) -> tuple[str, ...]:
    origins = settings.cors_origins_list
    if origins == ["*"]:
        return ()
    normalized = [_normalize_origin(o) for o in origins]
    return tuple(o for o in normalized if o)


def _enforcement_override(settings: Settings) -> bool | None:
    """The self-hosted opt-in, from ``Settings.csrf_enforced`` if it exists, else the env var."""
    configured = getattr(settings, "csrf_enforced", None)
    if isinstance(configured, bool):
        return configured
    raw = os.environ.get(ENFORCE_ENV_VAR)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def policy_for_settings(settings: Settings | None = None) -> CsrfPolicy:
    """Resolve the active policy from the application mode and configuration.

    * ``production`` - enforced. Sessions exist, the deployment is reachable from a browser, and
      ``CORS_ALLOW_ORIGINS`` is expected to be pinned.
    * ``self_hosted`` - configurable, default off. Registration and sessions are available, but the
      default deployment is a single operator on a private network with wildcard CORS, where the
      origin half of the check cannot function. Set ``DBW_CSRF_ENFORCED=1`` to turn it on.
    * ``demo`` - not applicable: ``allow_registration`` is False and access is anonymous, so there
      is no ambient authority for a cross-site request to borrow.
    """
    settings = settings or get_settings()
    mode = settings.mode
    origins = _origins_from(settings)
    override = _enforcement_override(settings)

    if mode is AppMode.PRODUCTION:
        enforced = True if override is None else override
        rationale = (
            "Production: cookie sessions with credentialed CORS, so state-changing requests "
            "require a double-submit token and a recognised Origin."
        )
    elif mode is AppMode.SELF_HOSTED:
        enforced = bool(override)
        rationale = (
            f"Self-hosted: enforcement is opt-in via {ENFORCE_ENV_VAR} and is currently "
            f"{'on' if enforced else 'off'}. The default deployment is a single operator on a "
            "private network, often with wildcard CORS."
        )
    else:
        enforced = False
        rationale = (
            "Demo mode has no login and no session cookie, so there is no ambient authority for a "
            "cross-site request to borrow. Not applicable rather than disabled."
        )

    return CsrfPolicy(
        mode=mode,
        enforced=enforced,
        # With no allowlist there is nothing to compare an Origin against, so say so instead of
        # running a check that admits everything.
        check_origin=enforced and bool(origins),
        allowed_origins=origins,
        rationale=rationale,
        cookie_secure=settings.cookie_secure,
    )


def issue_token() -> str:
    """A fresh token. 32 bytes from :mod:`secrets`, URL-safe so it survives a cookie value."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: str, *, secure: bool = True) -> None:
    """Set the double-submit cookie.

    ``httponly`` is **False** by design and this is the one place that is correct: the front end has
    to read this value to echo it in the request header. It is not a credential - possessing it
    shows only that the caller can read a cookie on our own origin, which is exactly the property being
    tested. ``SameSite=Lax`` keeps it from being sent on cross-site subrequests at all.
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def tokens_match(cookie_token: str | None, header_token: str | None) -> bool:
    """Constant-time comparison that also rejects short or absent values.

    The length floor exists so an empty or trivially guessable pair (``""`` == ``""``) can never
    satisfy the check.
    """
    if not cookie_token or not header_token:
        return False
    if len(cookie_token) < MIN_TOKEN_LENGTH:
        return False
    return hmac.compare_digest(cookie_token, header_token)


def check_request(
    *,
    method: str,
    policy: CsrfPolicy,
    origin: str | None = None,
    referer: str | None = None,
    cookie_token: str | None = None,
    header_token: str | None = None,
    has_session: bool = True,
    self_origin: str | None = None,
) -> CsrfResult:
    """Decide one request, from primitives only.

    Kept free of :class:`~fastapi.Request` so the decision table is testable without an HTTP stack;
    :func:`require_csrf` is the thin adapter that extracts these values from a live request.

    ``has_session`` defaults to ``True`` - the fail-closed default. A caller that cannot determine
    whether ambient authority is present gets the strict path.
    """
    if method.upper() in SAFE_METHODS:
        return CsrfResult(CsrfStatus.SAFE_METHOD, f"{method.upper()} is a safe method.")

    if not policy.enforced:
        return CsrfResult(CsrfStatus.NOT_ENFORCED, policy.rationale)

    if not has_session:
        return CsrfResult(
            CsrfStatus.NO_AMBIENT_AUTHORITY,
            "No session cookie: the request carries no ambient authority to abuse.",
        )

    checks: list[str] = []

    if policy.check_origin:
        checks.append("origin")
        candidate = _normalize_origin(origin) or _normalize_origin(referer)
        if candidate is None:
            # An Origin that was *sent* but does not reduce to a comparable origin - the literal
            # "null" from a sandboxed iframe or an opaque document - is a rejection, not an absence.
            # Conflating the two would hide the more interesting case in the logs.
            if (origin or "").strip() or (referer or "").strip():
                return CsrfResult(
                    CsrfStatus.ORIGIN_REJECTED,
                    "The Origin or Referer sent does not reduce to a comparable origin.",
                    tuple(checks),
                )
            # A cookie-bearing state-changing request with no Origin or Referer at all cannot be
            # distinguished from a forged one, so it is refused. Non-browser clients reach the
            # exemption above instead, because they send no session cookie.
            return CsrfResult(
                CsrfStatus.ORIGIN_MISSING,
                "A session-authenticated state-changing request must carry an Origin or Referer.",
                tuple(checks),
            )
        allowed = set(policy.allowed_origins)
        if policy.trust_self_origin:
            normalized_self = _normalize_origin(self_origin)
            if normalized_self:
                allowed.add(normalized_self)
        if candidate not in allowed:
            return CsrfResult(
                CsrfStatus.ORIGIN_REJECTED,
                f"Origin {candidate} is not in the allowlist for this deployment.",
                tuple(checks),
            )

    checks.append("token")
    if not cookie_token or not header_token:
        return CsrfResult(
            CsrfStatus.TOKEN_MISSING,
            f"Both the {policy.cookie_name} cookie and the "
            f"{policy.header_name} header are required.",
            tuple(checks),
        )
    if len(cookie_token) < MIN_TOKEN_LENGTH:
        return CsrfResult(
            CsrfStatus.TOKEN_TOO_SHORT,
            f"The CSRF token is shorter than the {MIN_TOKEN_LENGTH}-character minimum.",
            tuple(checks),
        )
    if not tokens_match(cookie_token, header_token):
        return CsrfResult(
            CsrfStatus.TOKEN_MISMATCH,
            "The CSRF header does not match the CSRF cookie.",
            tuple(checks),
        )

    return CsrfResult(CsrfStatus.OK, "Origin and double-submit token accepted.", tuple(checks))


class CsrfError(HTTPException):
    """403 with the decision attached, so the failure is diagnosable from the response alone."""

    def __init__(self, result: CsrfResult) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "csrf_failed",
                "status": result.status.value,
                "message": result.detail,
            },
        )
        self.result = result


def _session_cookie_names() -> tuple[str, ...]:
    """Both spellings of the session cookie: ``app/security/user_auth.py`` picks one per mode."""
    return ("__Host-dbw_session", "dbw_session")


def has_session_cookie(request: Request) -> bool:
    return any(request.cookies.get(name) for name in _session_cookie_names())


def evaluate_request(request: Request, policy: CsrfPolicy | None = None) -> CsrfResult:
    """Run :func:`check_request` against a live request. Does not raise - use it to observe."""
    active = policy or policy_for_settings()
    self_origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    return check_request(
        method=request.method,
        policy=active,
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        cookie_token=request.cookies.get(active.cookie_name),
        header_token=request.headers.get(active.header_name),
        has_session=has_session_cookie(request),
        self_origin=self_origin,
    )


def require_csrf(request: Request) -> CsrfResult:
    """FastAPI dependency: refuse the request when the CSRF check fails.

    A refusal is audited (:attr:`~app.platform.audit.AuditAction.CSRF_REJECTED`) because a blocked
    forgery attempt leaves no other trace, and an unexplained 403 in a user's browser is exactly the
    signal an operator needs to see correlated with a source address.
    """
    result = evaluate_request(request)
    if result.ok:
        return result

    record(
        AuditAction.CSRF_REJECTED,
        subject=request.url.path,
        outcome=AuditOutcome.DENIED,
        actor=AuditActor.anonymous(ip=request.client.host if request.client else None),
        reason=result.status.value,
        detail={"method": request.method, "origin": request.headers.get("origin")},
    )
    raise CsrfError(result)


__all__ = [
    "COOKIE_NAME",
    "ENFORCE_ENV_VAR",
    "HEADER_NAME",
    "SAFE_METHODS",
    "CsrfError",
    "CsrfPolicy",
    "CsrfResult",
    "CsrfStatus",
    "check_request",
    "clear_csrf_cookie",
    "evaluate_request",
    "has_session_cookie",
    "issue_token",
    "policy_for_settings",
    "require_csrf",
    "set_csrf_cookie",
    "tokens_match",
]
