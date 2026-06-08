"""Server-side sessions: an opaque cookie token maps to a DB row.

Only the SHA-256 of the token is stored, so a DB leak can't be replayed as a session.
Each public function accepts an optional ``db`` session (injected in tests); when omitted it
opens one against the project database.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from db.database_manager import get_project_db_connection_string, get_session
from db.model import UserSession

_TOKEN_BYTES = 32


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    user_id: int, *, ttl_seconds: int | None = None, db: Session | None = None
) -> str:
    """Create a session row and return the opaque token to set as a cookie."""
    ttl = ttl_seconds if ttl_seconds is not None else get_settings().session_ttl_seconds
    own = db is None
    db = db or get_session(get_project_db_connection_string())
    try:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        db.add(
            UserSession(
                token_hash=_hash_token(token),
                user_id=user_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            )
        )
        db.commit() if own else db.flush()
        return token
    finally:
        if own:
            db.close()


def resolve_session(token: str, *, db: Session | None = None) -> int | None:
    """Return the user id for a valid, unexpired token, else None."""
    if not token:
        return None
    own = db is None
    db = db or get_session(get_project_db_connection_string())
    try:
        row = db.execute(
            select(UserSession).where(UserSession.token_hash == _hash_token(token))
        ).scalar_one_or_none()
        if row is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:  # sqlite returns naive datetimes
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            return None
        return row.user_id
    finally:
        if own:
            db.close()


def revoke_session(token: str, *, db: Session | None = None) -> None:
    """Delete the session row for a token (logout)."""
    if not token:
        return
    own = db is None
    db = db or get_session(get_project_db_connection_string())
    try:
        row = db.execute(
            select(UserSession).where(UserSession.token_hash == _hash_token(token))
        ).scalar_one_or_none()
        if row is not None:
            db.delete(row)
            db.commit() if own else db.flush()
    finally:
        if own:
            db.close()
