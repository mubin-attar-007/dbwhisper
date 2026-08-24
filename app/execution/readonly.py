"""Does this credential actually lack write access?

The honest answer is often "we cannot be sure", so the result is a five-state enum rather than a
boolean. Everything is inspected through privilege metadata - **no probe ever attempts a write**.
Creating a table to "test" whether writes are possible would be exactly the behaviour this product
promises not to have.

============================  ==========================================================
``VERIFIED_READ_ONLY``        Privilege metadata was readable and shows no write grants.
``APPEARS_READ_ONLY``         Some checks ran, none found write access, but coverage was partial.
``WRITABLE``                  A write privilege, ownership or admin role was found.
``UNSUPPORTED``               The dialect has no non-destructive way to answer.
``CONNECTION_FAILED``         We could not connect at all.
============================  ==========================================================
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import text

from app.execution.connections import get_engine
from app.sqlpolicy.types import Dialect

logger = logging.getLogger(__name__)

WRITE_PRIVILEGES = frozenset(
    {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "DROP", "ALTER", "ALL PRIVILEGES", "ALL"}
)


class ReadOnlyStatus(StrEnum):
    VERIFIED_READ_ONLY = "verified_read_only"
    APPEARS_READ_ONLY = "appears_read_only"
    WRITABLE = "writable"
    UNSUPPORTED = "unsupported"
    CONNECTION_FAILED = "connection_failed"

    @property
    def is_safe(self) -> bool:
        return self in (ReadOnlyStatus.VERIFIED_READ_ONLY, ReadOnlyStatus.APPEARS_READ_ONLY)


@dataclass(slots=True)
class ReadOnlyReport:
    status: ReadOnlyStatus
    message: str
    checks_run: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def is_safe(self) -> bool:
        return self.status.is_safe


def verify_read_only(
    connection_string: str, dialect: Dialect | str | None = None
) -> ReadOnlyReport:
    """Inspect a connection's privileges without writing anything to the target database."""
    d = dialect if isinstance(dialect, Dialect) else Dialect.from_db_type(dialect)
    try:
        engine = get_engine(connection_string, d)
        connection = engine.connect()
    except Exception as exc:
        return ReadOnlyReport(
            ReadOnlyStatus.CONNECTION_FAILED,
            f"Could not connect to verify privileges ({type(exc).__name__}).",
        )

    try:
        if d is Dialect.GENERIC:
            d = Dialect.from_db_type(engine.dialect.name)
        if d is Dialect.POSTGRES:
            return _check_postgres(connection)
        if d is Dialect.MYSQL:
            return _check_mysql(connection)
        if d is Dialect.MSSQL:
            return _check_mssql(connection)
        if d is Dialect.SQLITE:
            return _check_sqlite(connection_string)
        return ReadOnlyReport(
            ReadOnlyStatus.UNSUPPORTED,
            f"No non-destructive privilege check exists for dialect '{engine.dialect.name}'.",
        )
    finally:
        try:
            connection.close()
        except Exception:  # pragma: no cover
            logger.debug("Closing the privilege-check connection failed", exc_info=True)


def _scalar(connection, sql: str) -> object | None:
    return connection.execute(text(sql)).scalar()


def _check_postgres(connection) -> ReadOnlyReport:
    report = ReadOnlyReport(ReadOnlyStatus.APPEARS_READ_ONLY, "")
    evidence = report.evidence

    def run(name: str, sql: str):
        try:
            value = _scalar(connection, sql)
            report.checks_run.append(name)
            evidence[name] = value
            return value
        except Exception as exc:
            report.checks_failed.append(name)
            logger.debug("Postgres privilege check %s failed: %s", name, exc)
            return None

    if run("superuser", "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
        return ReadOnlyReport(
            ReadOnlyStatus.WRITABLE,
            "The connection uses a PostgreSQL superuser.",
            report.checks_run,
            report.checks_failed,
            evidence,
        )
    if run(
        "create_on_database",
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')",
    ):
        return ReadOnlyReport(
            ReadOnlyStatus.WRITABLE,
            "The connection has CREATE privilege on the database.",
            report.checks_run,
            report.checks_failed,
            evidence,
        )
    write_grants = run(
        "write_table_grants",
        """
        SELECT count(*) FROM information_schema.role_table_grants
        WHERE grantee IN (SELECT rolname FROM pg_roles WHERE pg_has_role(current_user, oid, 'USAGE'))
          AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER')
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
        """,
    )
    if isinstance(write_grants, int) and write_grants > 0:
        return ReadOnlyReport(
            ReadOnlyStatus.WRITABLE,
            f"The connection holds {write_grants} write grant(s) on user tables.",
            report.checks_run,
            report.checks_failed,
            evidence,
        )
    owns = run(
        "owned_tables",
        """
        SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p') AND pg_has_role(current_user, c.relowner, 'USAGE')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        """,
    )
    if isinstance(owns, int) and owns > 0:
        return ReadOnlyReport(
            ReadOnlyStatus.WRITABLE,
            f"The connection owns {owns} table(s); owners can always write.",
            report.checks_run,
            report.checks_failed,
            evidence,
        )

    if write_grants is None or owns is None:
        return ReadOnlyReport(
            ReadOnlyStatus.APPEARS_READ_ONLY,
            "No write privileges were found, but some privilege views were unreadable.",
            report.checks_run,
            report.checks_failed,
            evidence,
        )
    return ReadOnlyReport(
        ReadOnlyStatus.VERIFIED_READ_ONLY,
        "No superuser, CREATE, ownership or table write grants were found.",
        report.checks_run,
        report.checks_failed,
        evidence,
    )


def _parse_mysql_grant(line: str) -> set[str]:
    """Privileges named between ``GRANT`` and ``ON`` - the database name never lands in the set."""
    upper = line.upper()
    if not upper.startswith("GRANT ") or " ON " not in upper:
        return set()
    body = upper[len("GRANT ") : upper.index(" ON ")]
    return {p.strip().split("(")[0].strip() for p in body.split(",") if p.strip()}


def _check_mysql(connection) -> ReadOnlyReport:
    try:
        rows = connection.execute(text("SHOW GRANTS FOR CURRENT_USER()")).all()
    except Exception as exc:
        return ReadOnlyReport(
            ReadOnlyStatus.UNSUPPORTED,
            f"SHOW GRANTS is not available for this account ({type(exc).__name__}).",
            checks_failed=["show_grants"],
        )
    granted: set[str] = set()
    for row in rows:
        granted |= _parse_mysql_grant(str(row[0]))
    writes = sorted(granted & WRITE_PRIVILEGES)
    evidence = {"privileges": sorted(granted)}
    if writes:
        return ReadOnlyReport(
            ReadOnlyStatus.WRITABLE,
            f"SHOW GRANTS lists write privileges: {', '.join(writes)}.",
            ["show_grants"],
            [],
            evidence,
        )
    return ReadOnlyReport(
        ReadOnlyStatus.VERIFIED_READ_ONLY,
        "SHOW GRANTS lists no write privileges.",
        ["show_grants"],
        [],
        evidence,
    )


def _check_mssql(connection) -> ReadOnlyReport:
    report = ReadOnlyReport(ReadOnlyStatus.APPEARS_READ_ONLY, "")

    def run(name: str, sql: str):
        try:
            value = _scalar(connection, sql)
            report.checks_run.append(name)
            report.evidence[name] = value
            return value
        except Exception as exc:
            report.checks_failed.append(name)
            logger.debug("SQL Server privilege check %s failed: %s", name, exc)
            return None

    for name, sql, reason in (
        ("sysadmin", "SELECT IS_SRVROLEMEMBER('sysadmin')", "the login is a sysadmin"),
        ("db_owner", "SELECT IS_ROLEMEMBER('db_owner')", "the user is a member of db_owner"),
        (
            "db_datawriter",
            "SELECT IS_ROLEMEMBER('db_datawriter')",
            "the user is a member of db_datawriter",
        ),
        (
            "db_ddladmin",
            "SELECT IS_ROLEMEMBER('db_ddladmin')",
            "the user is a member of db_ddladmin",
        ),
    ):
        if run(name, sql) == 1:
            return ReadOnlyReport(
                ReadOnlyStatus.WRITABLE,
                f"Write access is available: {reason}.",
                report.checks_run,
                report.checks_failed,
                report.evidence,
            )

    write_perms = run(
        "explicit_write_permissions",
        """
        SELECT COUNT(*) FROM sys.fn_my_permissions(NULL, 'DATABASE')
        WHERE permission_name IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CONTROL', 'CREATE TABLE')
        """,
    )
    if isinstance(write_perms, int) and write_perms > 0:
        return ReadOnlyReport(
            ReadOnlyStatus.WRITABLE,
            f"The database principal holds {write_perms} write permission(s).",
            report.checks_run,
            report.checks_failed,
            report.evidence,
        )
    if not report.checks_run:
        return ReadOnlyReport(
            ReadOnlyStatus.UNSUPPORTED,
            "None of the SQL Server role checks could be executed.",
            report.checks_run,
            report.checks_failed,
            report.evidence,
        )
    if write_perms is None:
        return ReadOnlyReport(
            ReadOnlyStatus.APPEARS_READ_ONLY,
            "No writer/owner role membership was found, but the permission list was unreadable.",
            report.checks_run,
            report.checks_failed,
            report.evidence,
        )
    return ReadOnlyReport(
        ReadOnlyStatus.VERIFIED_READ_ONLY,
        "No admin role membership and no write permissions were found.",
        report.checks_run,
        report.checks_failed,
        report.evidence,
    )


def _check_sqlite(connection_string: str) -> ReadOnlyReport:
    """For a file database, read-only means the URL says so or the file is not writable."""
    lowered = connection_string.lower()
    if "mode=ro" in lowered or "immutable=1" in lowered:
        return ReadOnlyReport(
            ReadOnlyStatus.VERIFIED_READ_ONLY,
            "The SQLite URL opens the database in read-only mode.",
            ["url_mode"],
        )
    path = connection_string.split("///", 1)[-1].split("?", 1)[0]
    if path in {":memory:", ""}:
        return ReadOnlyReport(
            ReadOnlyStatus.APPEARS_READ_ONLY,
            "In-memory SQLite database; the connection is opened with PRAGMA query_only.",
            ["in_memory"],
        )
    if os.path.exists(path) and not os.access(path, os.W_OK):
        return ReadOnlyReport(
            ReadOnlyStatus.VERIFIED_READ_ONLY,
            "The SQLite file is not writable by this process.",
            ["file_permissions"],
        )
    return ReadOnlyReport(
        ReadOnlyStatus.APPEARS_READ_ONLY,
        "The SQLite file is writable on disk; connections are opened with PRAGMA query_only, "
        "so writes are refused, but file permissions do not prove it.",
        ["file_permissions"],
    )


__all__ = ["WRITE_PRIVILEGES", "ReadOnlyReport", "ReadOnlyStatus", "verify_read_only"]
