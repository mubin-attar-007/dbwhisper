"""Auth foundation: Argon2id hashing + the user/session models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.security.passwords import hash_password, needs_rehash, verify_password
from db.model import Base, User, UserSession


def test_hash_and_verify_roundtrip() -> None:
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"  # not stored in plaintext
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery staple") is True
    assert verify_password(h, "wrong") is False


def test_verify_and_rehash_handle_malformed_hash() -> None:
    assert verify_password("not-a-real-hash", "x") is False
    assert needs_rehash("not-a-real-hash") is True


def test_user_and_session_models_roundtrip() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        user = User(email="a@example.com", password_hash=hash_password("pw"))
        session.add(user)
        session.commit()
        assert user.id is not None
        assert user.is_active is True
        assert user.is_admin is False

        sess = UserSession(
            token_hash="x" * 64,
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(sess)
        session.commit()
        assert sess.id is not None
        assert session.query(User).filter_by(email="a@example.com").count() == 1
    finally:
        session.close()
