"""The only code path that runs SQL against a target database.

Everything - generated SQL, user-edited SQL, an investigation sub-query, an evaluation case - comes
through :func:`execute`. There is deliberately no second entrance: a bypass is how a read-only
promise quietly stops being true.

Order of operations, all of it before a single row is fetched:

1. **Policy.** The statement is (re-)evaluated by :mod:`app.sqlpolicy`, even when the caller already
   holds a decision. A caller-supplied decision is accepted only as an *approval token*: its
   fingerprint must match the statement being run, which is what stops an approved query from being
   swapped for a different one after the fact.
2. **Network.** The target host is checked against the active network policy (SSRF).
3. **Session.** A read-only transaction with a statement timeout is opened, and rolled back at the
   end whatever happens.
4. **Fetch.** At most ``max_rows + 1`` rows are read, so truncation is detected rather than guessed.

Errors are translated before they leave: a driver exception can carry the DSN, the host and the
schema, none of which belongs in an API response.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.execution.connections import read_only_connection
from app.execution.results import ResultFrame, ResultStats, compute_stats
from app.platform.modes import NetworkPolicyLevel
from app.platform.network_policy import NetworkPolicyError, check_target
from app.sqlpolicy import Decision, Dialect, PolicyContext, PolicyDecision, PolicyLevel, evaluate
from app.sqlpolicy.limits import apply_pagination, count_wrapper
from app.sqlpolicy.parse import parse_single_statement
from app.sqlpolicy.scope_loader import scope_from_schema_index

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 1000
DEFAULT_TIMEOUT_SECONDS = 30


class ExecutionError(RuntimeError):
    """A failure that should be reported to the caller with a sanitized message."""

    def __init__(self, message: str, *, category: str = "execution") -> None:
        super().__init__(message)
        self.category = category


@dataclass(slots=True)
class ExecutionRequest:
    sql: str
    connection_string: str
    dialect: Dialect = Dialect.GENERIC
    db_flag: str | None = None
    max_rows: int = DEFAULT_MAX_ROWS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    page: int | None = None
    page_size: int | None = None
    include_total: bool = False
    policy_level: PolicyLevel = PolicyLevel.STANDARD
    network_level: NetworkPolicyLevel = NetworkPolicyLevel.PRIVATE_ALLOWED
    network_allowlist: list[str] = field(default_factory=list)
    bundled_hosts: list[str] = field(default_factory=list)
    approved_fingerprint: str | None = None
    require_scope: bool | None = None

    @property
    def paginated(self) -> bool:
        return self.page is not None and self.page_size is not None


@dataclass(slots=True)
class ExecutionResult:
    success: bool
    policy: PolicyDecision | None = None
    frame: ResultFrame | None = None
    stats: ResultStats | None = None
    error: str | None = None
    error_category: str | None = None
    read_only_enforced: bool = False
    read_only_note: str | None = None
    executed_sql: str | None = None

    @property
    def validation_passed(self) -> bool:
        return bool(self.policy and self.policy.decision is not Decision.DENY)


# ---------------------------------------------------------------------------------------------
# Error sanitization
# ---------------------------------------------------------------------------------------------

_DSN_PATTERN = re.compile(r"(?i)(postgres(?:ql)?|mysql|mssql|sqlite|duckdb)(\+\w+)?://\S+")
_KV_SECRET = re.compile(r"(?i)\b(password|pwd|uid|user|host|server|dsn|hostaddr)\s*=\s*[^\s;,)]+")
_MAX_ERROR_CHARS = 300

_FRIENDLY = (
    (
        re.compile(r"(?i)\b(does not exist|unknown column|no such column|invalid column)"),
        "unknown_object",
    ),
    (re.compile(r"(?i)\b(syntax error|near \")"), "syntax"),
    (
        re.compile(r"(?i)\b(permission denied|access denied|not authorized|denied to user)"),
        "permission",
    ),
    (
        re.compile(
            r"(?i)\b(timeout|timed out|canceling statement|query execution was interrupted)"
        ),
        "timeout",
    ),
    (re.compile(r"(?i)\b(read-only|cannot execute .* in a read-only transaction)"), "read_only"),
    (re.compile(r"(?i)\b(deadlock|lock wait|could not obtain lock)"), "lock"),
    (re.compile(r"(?i)\b(connection|could not connect|server closed)"), "connection"),
    (
        re.compile(r"(?i)\b(division by zero|numeric overflow|out of range|invalid input syntax)"),
        "data",
    ),
)


def sanitize_db_error(exc: BaseException) -> tuple[str, str]:
    """Return ``(message, category)`` safe to show a user.

    The driver's own text is kept - it is genuinely useful for fixing a query - but stripped of
    connection strings and credential-shaped key/value pairs, and truncated.
    """
    raw = getattr(exc, "orig", exc)
    message = " ".join(str(raw).split())
    message = _DSN_PATTERN.sub("<connection>", message)
    message = _KV_SECRET.sub(lambda m: f"{m.group(1)}=<redacted>", message)
    if len(message) > _MAX_ERROR_CHARS:
        message = message[:_MAX_ERROR_CHARS] + "..."
    category = "execution"
    for pattern, label in _FRIENDLY:
        if pattern.search(message):
            category = label
            break
    if category == "read_only":
        message = (
            "The database refused the statement because the session is read-only. "
            "DBWhisper only runs read queries."
        )
    return message or "The database reported an error.", category


# ---------------------------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------------------------


def _policy_context(request: ExecutionRequest) -> PolicyContext:
    scope = scope_from_schema_index(request.db_flag, request.dialect) if request.db_flag else None
    require_scope = (
        request.require_scope if request.require_scope is not None else bool(request.db_flag)
    )
    return PolicyContext(
        dialect=request.dialect,
        scope=scope,
        level=request.policy_level,
        default_row_limit=request.max_rows,
        inject_limit=True,
        require_scope=require_scope,
    )


def _audit_denial(request: ExecutionRequest, decision: PolicyDecision) -> None:
    """Record a statement the policy engine refused to run.

    The denial is the whole point of the engine, and it is the one outcome that leaves no other
    trace: a refused query never reaches the database, so without this there is nothing to review
    after the fact.

    What is recorded is deliberately *about* the statement rather than the statement itself - the
    class of statement, the rules that fired, and the policy version that decided - so a review can
    tell "someone tried to drop a table" from "someone hit the join limit" without the audit trail
    becoming a second copy of user input. The fingerprint is included when there is one; a statement
    refused at parse or classification time never gets that far, and recording ``None`` honestly is
    better than substituting the raw SQL to fill the field.
    """
    try:
        from app.platform.audit import AuditAction, AuditOutcome, record

        detail: dict[str, Any] = {
            "statement_type": decision.statement_type,
            "policy_version": decision.policy_version,
            "dialect": decision.dialect.value if decision.dialect else None,
            "rules": [r.rule_id for r in decision.rules if not r.passed][:5],
        }
        if decision.fingerprint:
            detail["fingerprint"] = decision.fingerprint

        record(
            AuditAction.POLICY_DENIED,
            subject=request.db_flag or "unknown",
            outcome=AuditOutcome.DENIED,
            reason=decision.reason or "refused by policy",
            detail=detail,
        )
    except Exception:  # pragma: no cover - auditing must never turn a denial into an allow
        logger.debug("Could not audit a policy denial", exc_info=True)


def execute(request: ExecutionRequest) -> ExecutionResult:
    """Validate, then run one read-only statement against a target database."""
    decision = evaluate(request.sql, _policy_context(request))

    if decision.decision is Decision.DENY:
        _audit_denial(request, decision)
        return ExecutionResult(
            success=False, policy=decision, error=decision.reason, error_category="policy"
        )
    if decision.decision is Decision.NEEDS_APPROVAL and request.approved_fingerprint is None:
        return ExecutionResult(
            success=False, policy=decision, error=decision.reason, error_category="needs_approval"
        )
    if request.approved_fingerprint and request.approved_fingerprint != decision.fingerprint:
        # The statement changed after it was approved. The old approval cannot authorise it.
        return ExecutionResult(
            success=False,
            policy=decision,
            error=(
                "The approval does not match this statement. Re-review the SQL before running it."
            ),
            error_category="approval_mismatch",
        )

    try:
        network = check_target(
            request.connection_string,
            level=request.network_level,
            allowlist=request.network_allowlist,
            bundled_hosts=request.bundled_hosts,
        )
        network.raise_if_denied()
    except NetworkPolicyError as exc:
        return ExecutionResult(
            success=False, policy=decision, error=str(exc), error_category="network_policy"
        )

    sql_to_run = decision.execution_sql
    if request.paginated:
        try:
            root = parse_single_statement(sql_to_run, request.dialect).expression
            sql_to_run = apply_pagination(
                root, request.dialect, request.page, request.page_size
            ).sql(dialect=request.dialect.sqlglot_name, comments=False)
        except ValueError as exc:
            return ExecutionResult(
                success=False, policy=decision, error=str(exc), error_category="pagination"
            )

    effective_cap = min(
        request.max_rows, request.page_size if request.page_size else request.max_rows
    )

    started = time.perf_counter()
    try:
        with read_only_connection(
            request.connection_string, request.dialect, timeout_seconds=request.timeout_seconds
        ) as (connection, setup):
            result = connection.execute(text(sql_to_run), decision.parameters or {})
            columns = list(result.keys())
            # One extra row tells us whether more exist without counting the whole table.
            rows = [tuple(row) for row in result.fetchmany(effective_cap + 1)]
            truncated = len(rows) > effective_cap
            if truncated:
                rows = rows[:effective_cap]
            elif _filled_the_cap(len(rows), decision.limit_applied, request):
                # The statement carried the cap itself, so the extra-row trick cannot fire: a full
                # page means there may be more rows the caller is not seeing.
                truncated = True
            result.close()

            total_rows: int | None = None
            if request.include_total:
                total_rows = _count_total(connection, decision, request)

        duration_ms = (time.perf_counter() - started) * 1000
        frame = ResultFrame(
            columns=columns,
            rows=rows,
            truncated=truncated,
            row_limit=effective_cap,
            duration_ms=duration_ms,
            total_rows=total_rows,
            page=request.page,
            page_size=request.page_size,
        )
        return ExecutionResult(
            success=True,
            policy=decision,
            frame=frame,
            stats=compute_stats(frame),
            read_only_enforced=setup.enforced,
            read_only_note=setup.note,
            executed_sql=sql_to_run,
        )
    except (SQLAlchemyError, DBAPIError) as exc:
        message, category = sanitize_db_error(exc)
        logger.warning("Query execution failed (%s): %s", category, message)
        return ExecutionResult(
            success=False,
            policy=decision,
            error=message,
            error_category=category,
            executed_sql=sql_to_run,
        )
    except Exception as exc:  # pragma: no cover - unexpected driver behaviour
        message, category = sanitize_db_error(exc)
        logger.exception("Unexpected execution failure")
        return ExecutionResult(
            success=False, policy=decision, error=message, error_category=category
        )


def _filled_the_cap(row_count: int, applied_limit: int | None, request: ExecutionRequest) -> bool:
    """True when the result exactly fills a limit we imposed, so more rows may exist.

    Relevant whenever the limit lives inside the statement (policy-injected, policy-lowered, or a
    pagination window): the database stops at the limit, so fetching one extra row can never reveal
    the overflow. A limit the *user* asked for and that we did not change is not truncation - they
    got exactly what they requested.
    """
    if row_count == 0:
        return False
    if request.paginated:
        return row_count >= (request.page_size or 0)
    return applied_limit is not None and row_count >= applied_limit


def _count_total(connection, decision: PolicyDecision, request: ExecutionRequest) -> int | None:
    """``SELECT COUNT(*)`` over the approved statement, built from its AST rather than by concatenation."""
    try:
        root = parse_single_statement(decision.execution_sql, request.dialect).expression
        sql = count_wrapper(root).sql(dialect=request.dialect.sqlglot_name, comments=False)
        value = connection.execute(text(sql), decision.parameters or {}).scalar()
        return int(value) if value is not None else None
    except Exception as exc:
        logger.debug("Total-row count skipped: %s", exc)
        return None


def execute_sql(
    sql: str,
    connection_string: str,
    *,
    db_type: str | None = None,
    db_flag: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> ExecutionResult:
    """Convenience wrapper for callers that hold loose values rather than a request object."""
    return execute(
        ExecutionRequest(
            sql=sql,
            connection_string=connection_string,
            dialect=Dialect.from_db_type(db_type),
            db_flag=db_flag,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )
    )


__all__ = [
    "DEFAULT_MAX_ROWS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ExecutionError",
    "ExecutionRequest",
    "ExecutionResult",
    "execute",
    "execute_sql",
    "sanitize_db_error",
]
