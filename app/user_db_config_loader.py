"""User database configuration loading utilities."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DatabaseSettings
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
            default_path = (
                PROJECT_ROOT / "database_schemas" / db_flag / "db_intro" / f"{db_flag}_intro.txt"
            )
            if default_path.exists():
                intro_template = str(default_path)

        db_settings = DatabaseSettings(
            connection_string=os.path.expandvars(db_row.connection_string),
            intro_template=intro_template,
            description=db_row.description,
            max_rows=db_row.max_rows,
            query_timeout=db_row.query_timeout,
            exclude_column_matches=db_row.exclude_column_matches,
            db_type=db_row.db_type,
            # Do not read enforcement flags from DB schema; those are configured
            # via the application or environment for now.
        )

        # Best-effort read-only connection check for safety (only warn if configured)
        try:
            if not _is_read_only_connection(db_settings.connection_string):
                if db_settings.warn_on_readonly_mismatch:
                    logger.warning(
                        "Connection string for db_flag=%s does not appear to be read-only. Ensure the DB user has only SELECT privileges.",
                        db_flag,
                    )
                else:
                    logger.debug(
                        "Read-only detection heuristics did not detect a read-only user for db_flag=%s. To enable warnings set warn_on_readonly_mismatch=True in the database settings.",
                        db_flag,
                    )
        except Exception:
            logger.debug(
                "Unable to validate read-only nature of connection string for db_flag=%s", db_flag
            )

        return db_settings
    finally:
        session.close()


def _is_read_only_connection(connection_string: str) -> bool:
    """Simple heuristic: look for 'readonly' or 'ro' in username or designate 'read only' in connection params.
    This is not a perfect check but it's useful as a safety hint.
    """
    if not connection_string:
        return False
    lower = connection_string.lower()
    if "readonly" in lower or "read_only" in lower or "ro_" in lower or "read-only" in lower:
        return True
    # Check for read-only parameter explicit flag in some providers
    if "applicationintent=readonly" in lower or "read only" in lower:
        return True
    # If username appears and contains 'read', accept
    # Basic heuristics only
    if "user=" in lower or "uid=" in lower:
        import re

        m = re.search(r"(?:user|uid)=([^;@]+)", lower)
        if m and "read" in m.group(1):
            return True
    return False


def dialect_read_only_check(connection_string: str, dialect: str | None = None) -> bool:
    """Try a dialect-specific, non-invasive query to detect read-only mode.

    - PostgreSQL: `SHOW transaction_read_only;` -> 'on' indicates read-only
    - MySQL: `SELECT @@global.read_only;` or `SELECT @@read_only;`
    - MSSQL: `SELECT DATABASEPROPERTYEX(DB_NAME(), 'Updateability');` (returns 'READ_WRITE' or 'READ_ONLY')

    If the DB engine isn't detectable or the check fails, return False.
    This function requires an active SQLAlchemy connection and is not run by default; it is optional.
    """
    try:
        from sqlalchemy import text

        from db import database_manager
    except Exception:
        return False

    try:
        conn = database_manager.get_connection(connection_string)
    except Exception:
        return False

    try:
        dialect_lower = (dialect or str(conn.dialect.name)).lower()
        if dialect_lower in ("postgres", "postgresql"):
            row = conn.execute(text("SHOW transaction_read_only"))
            val = row.scalar() if row else None
            if str(val).lower() in ("on", "true", "1"):
                return True
        elif dialect_lower in ("mysql", "mariadb"):
            row = conn.execute(text("SELECT @@read_only"))
            val = row.scalar() if row else None
            if str(val).lower() in ("on", "true", "1"):
                return True
        elif dialect_lower in ("mssql", "sqlserver"):
            # MSSQL returns e.g., 'READ_WRITE' or 'READ_ONLY'
            row = conn.execute(text("SELECT DATABASEPROPERTYEX(DB_NAME(), 'Updateability')"))
            val = row.scalar() if row else None
            if str(val).lower() == "read_only":
                return True
    except Exception:
        # Best-effort: do not raise; caller should handle
        return False
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    return False
