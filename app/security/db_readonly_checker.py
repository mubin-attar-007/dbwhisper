from __future__ import annotations

import contextlib

from sqlalchemy import text

from app.utils.logger import setup_logging
from db import database_manager

logger = setup_logging(__name__)


def _get_engine(connection_string: str):
    try:
        return database_manager.get_engine(connection_string)
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.debug(
            "Failed to get engine for readonly check: %s", sanitize_for_log(str(exc), max_len=400)
        )
        return None


def is_read_only_connection(connection_string: str, db_type: str | None = None) -> tuple[bool, str]:
    """Attempt to determine if the connection string maps to a read-only user.

    Returns (is_read_only, message). If we cannot determine definitively, returns
    (False, explanation). Uses non-destructive checks only.
    """
    engine = _get_engine(connection_string)
    if not engine:
        return False, "Unable to create engine"

    try:
        dialect = engine.name
    except Exception:
        dialect = (db_type or "").lower()

    conn = None
    try:
        conn = engine.connect()
        # MSSQL checks
        if dialect and "mssql" in dialect:
            checked_sysadmin = False
            checked_dbwriter = False
            try:
                res = conn.execute(
                    text("SELECT IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin")
                ).scalar()
                checked_sysadmin = True
                if res == 1:
                    return False, "User is sysadmin"
            except Exception:
                logger.debug("IS_SRVROLEMEMBER not available or failed")
            try:
                res = conn.execute(
                    text("SELECT IS_ROLEMEMBER('db_datawriter') AS is_dbwriter")
                ).scalar()
                checked_dbwriter = True
                if res == 1:
                    return False, "User is a db_datawriter"
            except Exception:
                logger.debug("IS_ROLEMEMBER not available or failed")
            # If at least one check ran and didn't surface write access, assume read-only.
            if checked_sysadmin or checked_dbwriter:
                return True, "No sysadmin or db_datawriter membership detected"
            return False, "Unable to determine MSSQL role membership"

        # PostgreSQL checks
        if dialect and ("postgresql" in dialect or "psycopg" in dialect):
            try:
                res = conn.execute(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                ).scalar()
                if res:
                    return False, "User is a Postgres superuser"
            except Exception:
                logger.debug("Failed to detect Postgres superuser via pg_roles")
            try:
                res = conn.execute(
                    text(
                        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
                    )
                ).scalar()
                if res:
                    return False, "User has CREATE database privilege"
            except Exception:
                logger.debug("Failed to check has_database_privilege")
            return True, "No superuser or CREATE privilege detected"

        # MySQL checks
        if dialect and "mysql" in dialect:
            try:
                rows = conn.execute(text("SHOW GRANTS FOR CURRENT_USER()")).all()
                grants_text = " ".join(str(r) for r in rows).lower()
                for bad in ("insert", "update", "delete", "drop", "alter", "create"):
                    if bad in grants_text:
                        return False, f"User has {bad} privileges"
                return True, "No obvious write privileges in SHOW GRANTS"
            except Exception:
                logger.debug("SHOW GRANTS failed or not available")
                return False, "Unable to verify MySQL grants"

        # Default fallback: a harmless role-level probe.
        try:
            res = conn.execute(text("SELECT current_user")).scalar()
            if res:
                return True, "No write privileges detected by generic check"
        except Exception:
            return False, "Unable to determine role privileges"
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.debug(
            "Error while checking db privileges: %s", sanitize_for_log(str(exc), max_len=400)
        )
        return False, "Error while checking db privileges"
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
    return False, "Unable to conclusively determine read-only status"


def dialect_read_only_check(connection_string: str, dialect: str | None = None) -> bool:
    """Try a dialect-specific, non-invasive query to detect read-only mode.

    - PostgreSQL: ``SHOW transaction_read_only`` -> 'on' indicates read-only
    - MySQL: ``SELECT @@read_only``
    - MSSQL: ``SELECT DATABASEPROPERTYEX(DB_NAME(), 'Updateability')`` -> 'READ_ONLY'

    If the engine isn't detectable or the check fails, returns False. Optional; not run
    by default.
    """
    try:
        conn = database_manager.get_connection(connection_string)
    except Exception:
        return False

    try:
        dialect_lower = (dialect or str(conn.dialect.name)).lower()
        if dialect_lower in ("postgres", "postgresql"):
            val = conn.execute(text("SHOW transaction_read_only")).scalar()
            if str(val).lower() in ("on", "true", "1"):
                return True
        elif dialect_lower in ("mysql", "mariadb"):
            val = conn.execute(text("SELECT @@read_only")).scalar()
            if str(val).lower() in ("on", "true", "1"):
                return True
        elif dialect_lower in ("mssql", "sqlserver"):
            val = conn.execute(
                text("SELECT DATABASEPROPERTYEX(DB_NAME(), 'Updateability')")
            ).scalar()
            if str(val).lower() == "read_only":
                return True
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    return False
