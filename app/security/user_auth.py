"""User-session auth: FastAPI dependencies + cookie helpers.

These are inert until endpoints use them, and the gating decision lives behind
``settings.user_auth_enabled`` — so wiring them in does not change behavior on its own.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, Response, status

from app.core.config import get_settings
from app.security.sessions import resolve_session
from db.database_manager import get_project_db_connection_string, get_session
from db.model import User


def _cookie_name() -> str:
    # The __Host- prefix hardens the cookie (HTTPS-only, path=/, no Domain) but only works
    # over HTTPS — so use it exactly when cookies are Secure.
    return "__Host-dbw_session" if get_settings().cookie_secure else "dbw_session"


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=_cookie_name(),
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=_cookie_name(), path="/")


def get_session_token(request: Request) -> str | None:
    return request.cookies.get(_cookie_name())


def get_current_user(request: Request) -> User | None:
    """Resolve the session cookie to a User, or None. Use as an optional dependency."""
    token = get_session_token(request)
    if not token:
        return None
    user_id = resolve_session(token)
    if user_id is None:
        return None
    db = get_session(get_project_db_connection_string())
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        db.expunge(user)  # detach so attributes stay usable after the session closes
        return user
    finally:
        db.close()


def require_user(request: Request) -> User:
    """Dependency that 401s when there is no valid session."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    return user
