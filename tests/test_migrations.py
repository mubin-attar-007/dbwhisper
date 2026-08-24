"""Alembic migrations: fresh upgrade, idempotent re-run, and repair of a pre-Alembic database."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from db.migrate import current_revision, upgrade_to_head

EXPECTED_TABLES = {"users", "user_sessions", "Database_config", "verified_queries"}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_fresh_database_upgrades_to_head(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/fresh.db"
    upgrade_to_head(url)
    assert _tables(url) >= EXPECTED_TABLES
    assert current_revision(url) is not None
    upgrade_to_head(url)  # idempotent
    assert _tables(url) >= EXPECTED_TABLES


def test_pre_alembic_database_is_adopted_and_repaired(tmp_path):
    """A DB created by create_all (even one missing owner_id) must be adopted without errors."""
    url = f"sqlite:///{tmp_path.as_posix()}/legacy.db"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                'CREATE TABLE "Database_config" (id INTEGER PRIMARY KEY, db_flag VARCHAR(100) '
                "NOT NULL, db_type VARCHAR(50) NOT NULL, connection_string TEXT NOT NULL, "
                "description TEXT, max_rows INTEGER NOT NULL DEFAULT 1000, query_timeout INTEGER "
                "NOT NULL DEFAULT 30, intro_template TEXT, exclude_column_matches BOOLEAN NOT NULL "
                "DEFAULT 0, schema_extracted BOOLEAN NOT NULL DEFAULT 0, schema_extraction_date "
                "DATETIME)"
            )
        )
        conn.execute(
            text(
                'INSERT INTO "Database_config" (db_flag, db_type, connection_string) '
                "VALUES ('demo', 'postgres', 'x')"
            )
        )
    engine.dispose()

    upgrade_to_head(url)

    assert _tables(url) >= EXPECTED_TABLES
    engine = create_engine(url)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("Database_config")}
        assert "owner_id" in cols
        with engine.connect() as conn:
            assert conn.execute(text('SELECT count(*) FROM "Database_config"')).scalar() == 1
    finally:
        engine.dispose()


def _head_revision() -> str:
    from alembic.script import ScriptDirectory

    from db.migrate import alembic_config

    return ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()


def test_create_metadata_tables_uses_alembic(tmp_path):
    from db.database_manager import create_metadata_tables

    url = f"sqlite:///{tmp_path.as_posix()}/via_manager.db"
    create_metadata_tables(url)
    assert _tables(url) >= EXPECTED_TABLES
    assert current_revision(url) == _head_revision()


def test_connection_secret_column_is_added(tmp_path):
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{tmp_path.as_posix()}/secret.db"
    upgrade_to_head(url)
    engine = create_engine(url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("Database_config")}
    finally:
        engine.dispose()
    assert "connection_secret" in columns
    assert "connection_string" in columns, "the legacy column stays for one release"
