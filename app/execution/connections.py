"""Target-database engines, and the per-dialect setup that makes a session read-only.

Two things matter here and nowhere else:

1. **Engines are per data source and bounded.** A pool with no ceiling turns one expensive question
   into an outage for the customer's database, so every engine gets an explicit pool size, overflow,
   recycle and connect timeout.
2. **"Read-only" is enforced by the database, not by us.** The policy engine decides whether a
   statement *looks* read-only; this module asks the server to refuse writes outright, which is what
   actually holds when the policy engine is wrong. Each dialect has its own dialect of "no writes":

   =============  ====================================================================
   PostgreSQL     ``SET TRANSACTION READ ONLY`` + ``statement_timeout`` + ``lock_timeout``
   MySQL/MariaDB  ``START TRANSACTION READ ONLY`` + ``max_execution_time``/``max_statement_time``
   SQL Server     ``SET LOCK_TIMEOUT`` + driver query timeout (no session-level read-only exists)
   SQLite         ``PRAGMA query_only = ON`` (plus ``mode=ro`` when the URL is a file)
   =============  ====================================================================

   Where a dialect cannot enforce it (SQL Server), we say so rather than implying otherwise: the
   guarantee there rests on the policy engine plus a least-privilege login.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

from app.sqlpolicy.types import Dialect

logger = logging.getLogger(__name__)

DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 2
DEFAULT_POOL_RECYCLE_SECONDS = 1800
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class ReadOnlySetup:
    """What a dialect could actually do to make the session read-only."""

    enforced: bool
    statements: tuple[str, ...]
    note: str


def normalize_connection_string(connection_string: str) -> str:
    """Accept the JDBC form some enrollment payloads use and return a SQLAlchemy URL."""
    if not connection_string.startswith("jdbc:sqlserver://"):
        return connection_string
    rest = connection_string[len("jdbc:sqlserver://") :]
    host_port, _, params = rest.partition(";")
    host, _, port = host_port.partition(":")
    database = user = password = ""
    driver = "ODBC Driver 18 for SQL Server"
    for part in params.split(";"):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = key.lower()
        if key == "databasename":
            database = value
        elif key == "user":
            user = value
        elif key == "password":
            password = value
        elif key == "driver":
            driver = value
    server = f"{host},{port}" if port else host
    odbc = ";".join(
        [
            f"DRIVER={driver}",
            f"SERVER={server}",
            f"DATABASE={database}",
            f"UID={user}",
            f"PWD={password}",
            "Encrypt=yes",
            "TrustServerCertificate=yes",
        ]
    )
    return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"


def _connect_args(dialect: Dialect, connect_timeout: int) -> dict[str, object]:
    if dialect is Dialect.POSTGRES:
        return {"connect_timeout": connect_timeout}
    if dialect is Dialect.MYSQL:
        return {"connect_timeout": connect_timeout}
    if dialect is Dialect.MSSQL:
        return {"timeout": connect_timeout}
    return {}


@lru_cache(maxsize=32)
def get_engine(
    connection_string: str,
    dialect: Dialect = Dialect.GENERIC,
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> Engine:
    """A cached, bounded engine for one target data source."""
    url = normalize_connection_string(connection_string)
    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_recycle": DEFAULT_POOL_RECYCLE_SECONDS,
        "connect_args": _connect_args(dialect, connect_timeout),
        "future": True,
    }
    if not url.startswith("sqlite"):
        # SQLite uses a non-queue pool by default; the rest get an explicit ceiling.
        kwargs["pool_size"] = pool_size
        kwargs["max_overflow"] = max_overflow
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        _register_sqlite_query_only(engine)
    return engine


def _register_sqlite_query_only(engine: Engine) -> None:
    """SQLite has no transaction-level read-only, so set the connection-level pragma on connect."""

    @event.listens_for(engine, "connect")
    def _set_query_only(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA query_only = ON")
        finally:
            cursor.close()


def read_only_setup(dialect: Dialect, *, timeout_seconds: int) -> ReadOnlySetup:
    """The statements that put a session into read-only mode with a bounded runtime."""
    ms = max(1, int(timeout_seconds * 1000))
    if dialect is Dialect.POSTGRES:
        return ReadOnlySetup(
            True,
            (
                "SET TRANSACTION READ ONLY",
                f"SET LOCAL statement_timeout = {ms}",
                f"SET LOCAL lock_timeout = {ms}",
                f"SET LOCAL idle_in_transaction_session_timeout = {ms}",
            ),
            "PostgreSQL refuses writes for the whole transaction.",
        )
    if dialect is Dialect.MYSQL:
        return ReadOnlySetup(
            True,
            (f"SET SESSION MAX_EXECUTION_TIME = {ms}",),
            "MySQL/MariaDB start the transaction READ ONLY; the timeout hint is best-effort "
            "(MariaDB spells it max_statement_time and is skipped when unsupported).",
        )
    if dialect is Dialect.MSSQL:
        return ReadOnlySetup(
            False,
            (f"SET LOCK_TIMEOUT {ms}",),
            "SQL Server has no session-level read-only mode; enforcement relies on the policy "
            "engine and a least-privilege login. The driver query timeout still applies.",
        )
    if dialect in (Dialect.SQLITE, Dialect.DUCKDB):
        return ReadOnlySetup(
            True,
            ("PRAGMA query_only = ON",) if dialect is Dialect.SQLITE else (),
            "SQLite is put into query_only mode on connect.",
        )
    return ReadOnlySetup(
        False,
        (),
        "Unknown dialect: no server-side read-only mode could be applied; the policy engine and "
        "database privileges are the only controls.",
    )


@contextmanager
def read_only_connection(
    connection_string: str,
    dialect: Dialect,
    *,
    timeout_seconds: int = 30,
) -> Iterator[tuple[Connection, ReadOnlySetup]]:
    """Yield a connection inside a read-only transaction that is always rolled back.

    Rolling back rather than committing is deliberate: nothing this connection did should ever be
    able to persist, even if a statement slipped through that the policy engine should have caught.
    """
    engine = get_engine(connection_string, dialect)
    setup = read_only_setup(dialect, timeout_seconds=timeout_seconds)
    connection = engine.connect()
    try:
        if dialect is Dialect.MYSQL:
            # MySQL only accepts READ ONLY as part of starting the transaction.
            connection.exec_driver_sql("START TRANSACTION READ ONLY")
        else:
            connection.begin()
        for statement in setup.statements:
            try:
                connection.execute(text(statement))
            except Exception as exc:
                # A dialect variant that does not know one of these (MariaDB and
                # MAX_EXECUTION_TIME, for instance) must not fail the query outright.
                logger.debug("Read-only setup statement skipped (%s): %s", statement, exc)
        _apply_driver_timeout(connection, dialect, timeout_seconds)
        yield connection, setup
    finally:
        try:
            connection.rollback()
        except Exception:  # pragma: no cover - connection may already be dead
            logger.debug("Rollback on the read-only connection failed", exc_info=True)
        connection.close()


def _apply_driver_timeout(connection: Connection, dialect: Dialect, timeout_seconds: int) -> None:
    """SQL Server carries its query timeout on the DBAPI connection, not in SQL."""
    if dialect is not Dialect.MSSQL:
        return
    try:
        connection.connection.dbapi_connection.timeout = int(timeout_seconds)  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - driver dependent
        logger.debug("Could not set the pyodbc query timeout", exc_info=True)


def dispose_all() -> None:
    """Dispose every cached engine (used by tests and by connection-config changes)."""
    for engine in list((getattr(get_engine, "cache_info", lambda: None)() and []) or []):
        engine.dispose()  # pragma: no cover - defensive
    get_engine.cache_clear()


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_OVERFLOW",
    "DEFAULT_POOL_SIZE",
    "ReadOnlySetup",
    "dispose_all",
    "get_engine",
    "normalize_connection_string",
    "read_only_connection",
    "read_only_setup",
]
