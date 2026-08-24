"""Versioned, table-driven rule sets: statement classes, dangerous functions, system catalogs.

Changing anything in this module is a policy change: bump ``RULESET_VERSION`` and add a regression
case to ``app/evaluation/datasets/adversarial/sql_policy_cases.yaml``.
"""

from __future__ import annotations

from sqlglot import exp

from app.sqlpolicy.types import Dialect

RULESET_VERSION = "2.0.0"

# ---------------------------------------------------------------------------------------------
# Statement classes
# ---------------------------------------------------------------------------------------------

#: Root node types that may be executed.
ALLOWED_ROOT_TYPES: tuple[type[exp.Expression], ...] = (exp.Select, exp.SetOperation)

#: Node types that must not appear *anywhere* in the tree (covers writes hidden in CTEs/subqueries,
#: SELECT INTO, row locks, session changes, transaction control and every "unsupported syntax"
#: fallback sqlglot represents as ``Command``).
DENIED_NODE_TYPES: tuple[type[exp.Expression], ...] = tuple(
    t
    for t in (
        getattr(exp, name, None)
        for name in (
            "Insert",
            "Update",
            "Delete",
            "Merge",
            "Create",
            "Drop",
            "Alter",
            "TruncateTable",
            "Command",
            "Grant",
            "Revoke",
            "Set",
            "Transaction",
            "Commit",
            "Rollback",
            "Copy",
            "Use",
            "Lock",
            "Into",
            "Describe",
            "Show",
            "Pragma",
            "Analyze",
            "Cache",
            "Uncache",
            "LoadData",
            "Kill",
            "Declare",
            "Execute",
            "Attach",
            "Detach",
            "Refresh",
            "Comment",
            "Directory",
            "Put",
            "Get",
            "Export",
            "Summarize",
            "Install",
            "Parameter",
            "SessionParameter",
            "Placeholder",
        )
    )
    if isinstance(t, type)
)

_NODE_MESSAGES: dict[str, str] = {
    "Insert": "INSERT is not permitted",
    "Update": "UPDATE is not permitted",
    "Delete": "DELETE is not permitted",
    "Merge": "MERGE is not permitted",
    "Create": "CREATE / SELECT INTO new objects is not permitted",
    "Drop": "DROP is not permitted",
    "Alter": "ALTER is not permitted",
    "TruncateTable": "TRUNCATE is not permitted",
    "Command": "Unsupported or administrative statement is not permitted",
    "Grant": "GRANT is not permitted",
    "Revoke": "REVOKE is not permitted",
    "Set": "SET is not permitted",
    "Transaction": "Transaction control is not permitted",
    "Commit": "Transaction control is not permitted",
    "Rollback": "Transaction control is not permitted",
    "Copy": "COPY is not permitted",
    "Use": "USE is not permitted",
    "Lock": "Row locking clauses (FOR UPDATE / FOR SHARE / LOCK IN SHARE MODE) are not permitted",
    "Into": "SELECT ... INTO is not permitted",
    "Execute": "EXEC / EXECUTE is not permitted",
    "Parameter": "Parameter placeholders are not supported (provide literal values)",
    "SessionParameter": "Session/global variables are not permitted",
    "Placeholder": "Parameter placeholders are not supported (provide literal values)",
}


def denied_node_message(node: exp.Expression) -> str:
    name = type(node).__name__
    return _NODE_MESSAGES.get(name, f"{name} statements are not permitted")


# ---------------------------------------------------------------------------------------------
# Functions that read files, reach the network/OS, sleep, lock, change sequences or reveal config
# ---------------------------------------------------------------------------------------------

BLOCKED_FUNCTIONS: dict[Dialect, frozenset[str]] = {
    Dialect.GENERIC: frozenset(
        {
            "sleep",
            "benchmark",
            "load_file",
            "pg_sleep",
            "pg_sleep_for",
            "pg_sleep_until",
            "pg_read_file",
            "pg_read_binary_file",
            "pg_ls_dir",
            "pg_stat_file",
            "pg_ls_logdir",
            "pg_ls_waldir",
            "lo_import",
            "lo_export",
            "lo_unlink",
            "lo_create",
            "dblink",
            "dblink_exec",
            "dblink_connect",
            "dblink_open",
            "pg_terminate_backend",
            "pg_cancel_backend",
            "pg_reload_conf",
            "pg_rotate_logfile",
            "pg_advisory_lock",
            "pg_advisory_xact_lock",
            "pg_try_advisory_lock",
            "pg_notify",
            "current_setting",
            "set_config",
            "nextval",
            "setval",
            "xp_cmdshell",
            "xp_dirtree",
            "xp_fileexist",
            "xp_regread",
            "xp_regwrite",
            "xp_servicecontrol",
            "openrowset",
            "openquery",
            "opendatasource",
            "sp_executesql",
            "sp_oacreate",
            "sp_oamethod",
            "fn_xe_file_target_read_file",
            "fn_get_audit_file",
            "get_lock",
            "release_lock",
            "release_all_locks",
            "is_free_lock",
            "is_used_lock",
            "master_pos_wait",
            "source_pos_wait",
            "sys_exec",
            "sys_eval",
            "load_extension",
            "readfile",
            "writefile",
            "edit",
            "fts3_tokenizer",
            "waitfor",
            "serverproperty",
            "has_perms_by_name",
            "read_csv",
            "read_csv_auto",
            "read_parquet",
            "read_json",
            "read_json_auto",
            "glob",
            "copy",
            "install",
            "load",
        }
    ),
}

#: Function-name prefixes that are denied per dialect (extended stored procedures etc.).
BLOCKED_FUNCTION_PREFIXES: dict[Dialect, tuple[str, ...]] = {
    Dialect.GENERIC: ("xp_", "sp_", "dblink", "pg_ls_", "pg_read_"),
    Dialect.MSSQL: ("xp_", "sp_", "fn_trace", "fn_xe"),
    Dialect.POSTGRES: ("pg_ls_", "pg_read_", "dblink", "lo_", "pg_logical_", "pg_replication_"),
}


def blocked_functions_for(dialect: Dialect) -> frozenset[str]:
    return BLOCKED_FUNCTIONS[Dialect.GENERIC] | BLOCKED_FUNCTIONS.get(dialect, frozenset())


def blocked_prefixes_for(dialect: Dialect) -> tuple[str, ...]:
    return BLOCKED_FUNCTION_PREFIXES[Dialect.GENERIC] + BLOCKED_FUNCTION_PREFIXES.get(dialect, ())


# ---------------------------------------------------------------------------------------------
# System catalogs
# ---------------------------------------------------------------------------------------------

SYSTEM_SCHEMAS: frozenset[str] = frozenset(
    {
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "pg_temp",
        "pg_internal",
        "sys",
        "mysql",
        "performance_schema",
        "master",
        "msdb",
        "tempdb",
        "model",
        "sqlite_temp",
        "duckdb_",
    }
)

#: Catalog relations that are reachable without a schema qualifier.
SYSTEM_TABLES: frozenset[str] = frozenset(
    {
        "pg_tables",
        "pg_views",
        "pg_class",
        "pg_namespace",
        "pg_attribute",
        "pg_proc",
        "pg_roles",
        "pg_user",
        "pg_shadow",
        "pg_authid",
        "pg_settings",
        "pg_database",
        "pg_stat_activity",
        "pg_stat_statements",
        "pg_stats",
        "pg_indexes",
        "pg_locks",
        "pg_hba_file_rules",
        "pg_file_settings",
        "pg_config",
        "sqlite_master",
        "sqlite_schema",
        "sqlite_temp_master",
        "sqlite_temp_schema",
        "sqlite_sequence",
        "sysobjects",
        "syscolumns",
        "sysusers",
        "syslogins",
        "sysdatabases",
        "sysprocesses",
        "duckdb_settings",
        "duckdb_tables",
        "duckdb_columns",
    }
)

SYSTEM_TABLE_PREFIXES: tuple[str, ...] = ("pg_", "sqlite_", "duckdb_", "sys", "information_schema")


def is_system_object(schema: str | None, table: str) -> bool:
    s = (schema or "").lower()
    t = table.lower()
    if s and (s in SYSTEM_SCHEMAS or s.startswith("pg_") or s.startswith("duckdb_")):
        return True
    if t in SYSTEM_TABLES:
        return True
    return not s and t.startswith(SYSTEM_TABLE_PREFIXES)


__all__ = [
    "ALLOWED_ROOT_TYPES",
    "BLOCKED_FUNCTIONS",
    "BLOCKED_FUNCTION_PREFIXES",
    "DENIED_NODE_TYPES",
    "RULESET_VERSION",
    "SYSTEM_SCHEMAS",
    "SYSTEM_TABLES",
    "blocked_functions_for",
    "blocked_prefixes_for",
    "denied_node_message",
    "is_system_object",
]
