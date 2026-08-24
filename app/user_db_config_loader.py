"""User database configuration loading utilities."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DatabaseSettings
from app.platform.connection_secrets import read_connection_string
from app.platform.paths import intro_path
from app.utils.logger import sanitize_for_log, setup_logging
from db.database_manager import get_project_db_connection_string, get_session
from db.model import DatabaseConfig

logger = setup_logging(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(path: str) -> str:
    if not path:
        return ""
    candidate = Path(path)
    resolved = candidate if candidate.is_absolute() else (PROJECT_ROOT / path).resolve()
    return str(resolved)


def _project_session() -> Session:
    project_connection = get_project_db_connection_string()
    return get_session(project_connection)


def get_user_database_settings(db_flag: str) -> DatabaseSettings:
    """Fetch a DatabaseSettings instance from the DatabaseConfig table for a user database."""
    session = _project_session()
    try:
        db_row = session.query(DatabaseConfig).filter_by(db_flag=db_flag).first()
        if not db_row:
            available = [row.db_flag for row in session.query(DatabaseConfig.db_flag).all()]
            raise KeyError(f"Unknown database flag '{db_flag}'. Available: {available}")
        logger.info("Fetched user database settings for db_flag=%s from DatabaseConfig", db_flag)
        logger.debug(
            "Database connection string (masked) for db_flag=%s: %s",
            db_flag,
            sanitize_for_log(os.path.expandvars(db_row.connection_string)),
        )
        intro_template = ""
        if db_row.intro_template:
            resolved = _resolve_path(db_row.intro_template)
            if Path(resolved).exists():
                intro_template = resolved
            else:
                fallback = (
                    PROJECT_ROOT
                    / "database_schemas"
                    / db_flag
                    / "db_intro"
                    / Path(db_row.intro_template).name
                )
                if fallback.exists():
                    intro_template = str(fallback)
        else:
            default_path = intro_path(db_flag)
            if default_path.exists():
                intro_template = str(default_path)

        # Prefer the encrypted column; fall back to the legacy plaintext one for rows that have
        # not been migrated yet. expandvars is deliberately NOT applied to a decrypted secret -
        # it would let a stored '%POSTGRES_CONNECTION_STRING%' pull in the application's own DSN.
        resolved = read_connection_string(
            secret=getattr(db_row, "connection_secret", None),
            plaintext=db_row.connection_string,
        )
        db_settings = DatabaseSettings(
            connection_string=resolved,
            intro_template=intro_template,
            description=db_row.description,
            max_rows=db_row.max_rows,
            query_timeout=db_row.query_timeout,
            exclude_column_matches=db_row.exclude_column_matches,
            db_type=db_row.db_type,
            # Do not read enforcement flags from DB schema; those are configured
            # via the application or environment for now.
        )

        return db_settings
    finally:
        session.close()
