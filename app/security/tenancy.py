"""Multi-tenancy access checks for enrolled databases.

A ``DatabaseConfig`` with ``owner_id IS NULL`` is PUBLIC (e.g. the shared demo) and readable by
anyone; otherwise only its owner may access it. Enforcement is wired into the endpoints behind
``settings.user_auth_enabled`` in a later step — this module is the policy, not the gate.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from db.database_manager import get_project_db_connection_string, get_session
from db.model import DatabaseConfig


def get_db_owner(db_flag: str, *, db: Session | None = None) -> tuple[bool, int | None]:
    """Return ``(exists, owner_id)`` for a db_flag; ``owner_id`` None means public."""
    own = db is None
    db = db or get_session(get_project_db_connection_string())
    try:
        cfg = db.query(DatabaseConfig).filter(DatabaseConfig.db_flag == db_flag).first()
        if cfg is None:
            return False, None
        return True, cfg.owner_id
    finally:
        if own:
            db.close()


def user_can_access_db_flag(
    db_flag: str, user_id: int | None, *, db: Session | None = None
) -> bool:
    """True if the user (or anonymous, ``user_id=None``) may access the db_flag.

    Public (owner_id NULL) → anyone. Owned → only the owner. Unknown flag → False.
    """
    exists, owner_id = get_db_owner(db_flag, db=db)
    if not exists:
        return False
    if owner_id is None:
        return True  # public
    return user_id is not None and owner_id == user_id
