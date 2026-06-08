"""Tenancy access policy: public vs owned database configs."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.security.tenancy import get_db_owner, user_can_access_db_flag
from db.model import Base, DatabaseConfig, User


def _mkdb():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_public_db_accessible_by_anyone() -> None:
    db = _mkdb()
    try:
        db.add(
            DatabaseConfig(db_flag="demo", db_type="postgres", connection_string="x", owner_id=None)
        )
        db.commit()
        assert user_can_access_db_flag("demo", None, db=db) is True  # anonymous
        assert user_can_access_db_flag("demo", 5, db=db) is True  # any logged-in user
    finally:
        db.close()


def test_owned_db_only_accessible_by_owner() -> None:
    db = _mkdb()
    try:
        owner = User(email="o@x.com", password_hash="h")
        db.add(owner)
        db.commit()
        db.add(
            DatabaseConfig(
                db_flag="mine", db_type="postgres", connection_string="x", owner_id=owner.id
            )
        )
        db.commit()
        assert user_can_access_db_flag("mine", owner.id, db=db) is True
        assert user_can_access_db_flag("mine", owner.id + 1, db=db) is False
        assert user_can_access_db_flag("mine", None, db=db) is False  # anonymous
    finally:
        db.close()


def test_unknown_flag_denied() -> None:
    db = _mkdb()
    try:
        assert user_can_access_db_flag("nope", 1, db=db) is False
        exists, owner = get_db_owner("nope", db=db)
        assert exists is False
        assert owner is None
    finally:
        db.close()
