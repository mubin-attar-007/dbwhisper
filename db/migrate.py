"""Programmatic Alembic runner used at startup and in tests.

``upgrade_to_head`` is idempotent and safe to call on every boot. It replaces the implicit
``Base.metadata.create_all`` self-heal with a versioned, reviewable migration history while keeping
the same "repair on deploy" behaviour. It only ever touches the **application** database.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def alembic_config(connection_string: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", connection_string)
    cfg.attributes["configure_logger"] = False
    cfg.attributes["url_is_explicit"] = True
    return cfg


def upgrade_to_head(connection_string: str) -> None:
    """Apply all pending migrations to the application database."""
    command.upgrade(alembic_config(connection_string), "head")
    logger.info("Application database migrated to head.")


def current_revision(connection_string: str) -> str | None:
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    engine = create_engine(connection_string)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


__all__ = ["alembic_config", "current_revision", "upgrade_to_head"]
