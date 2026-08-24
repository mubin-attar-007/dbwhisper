"""Alembic environment for the DBWhisper application database.

The connection URL comes from (in order): ``-x url=...`` on the CLI, the ``DBW_ALEMBIC_URL``
environment variable, ``PROJECT_DB_CONNECTION_STRING``, ``POSTGRES_CONNECTION_STRING``. Target
databases enrolled by users are **never** migrated by this code.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the job models registers them on Base.metadata so autogenerate sees them.
import app.jobs.models  # noqa: F401  (side-effect import)
from db.model import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    for candidate in (
        x_args.get("url"),
        os.getenv("DBW_ALEMBIC_URL"),
        os.getenv("PROJECT_DB_CONNECTION_STRING"),
        os.getenv("POSTGRES_CONNECTION_STRING"),
    ):
        if candidate:
            return candidate
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    # A programmatic caller (db/migrate.py) sets sqlalchemy.url explicitly; the CLI path resolves it.
    if not config.attributes.get("url_is_explicit"):
        section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-safe ALTERs in tests
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
