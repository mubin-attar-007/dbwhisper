"""Session create/resolve/revoke + expiry (sqlite-backed via the db injection seam)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.security.sessions import _hash_token, create_session, resolve_session, revoke_session
from db.model import Base, User, UserSession


def _mkdb():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_hash_token_is_sha256_hex() -> None:
    assert len(_hash_token("abc")) == 64
    assert _hash_token("abc") == _hash_token("abc")
    assert _hash_token("abc") != _hash_token("abd")


def test_session_roundtrip_and_revoke() -> None:
    db = _mkdb()
    try:
        user = User(email="x@y.z", password_hash="h")
        db.add(user)
        db.commit()

        token = create_session(user.id, ttl_seconds=3600, db=db)
        db.commit()
        assert resolve_session(token, db=db) == user.id

        revoke_session(token, db=db)
        db.commit()
        assert resolve_session(token, db=db) is None
    finally:
        db.close()


def test_expired_session_resolves_none() -> None:
    db = _mkdb()
    try:
        user = User(email="x@y.z", password_hash="h")
        db.add(user)
        db.commit()
        db.add(
            UserSession(
                token_hash=_hash_token("tok"),
                user_id=user.id,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        db.commit()
        assert resolve_session("tok", db=db) is None
    finally:
        db.close()
