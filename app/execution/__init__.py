"""The read-only execution layer: connections, privilege verification, and the single SQL path."""

from app.execution.readonly import ReadOnlyReport, ReadOnlyStatus, verify_read_only
from app.execution.results import ResultFrame, ResultStats, compute_stats
from app.execution.service import ExecutionRequest, ExecutionResult, execute, execute_sql

__all__ = [
    "ExecutionRequest",
    "ExecutionResult",
    "ReadOnlyReport",
    "ReadOnlyStatus",
    "ResultFrame",
    "ResultStats",
    "compute_stats",
    "execute",
    "execute_sql",
    "verify_read_only",
]
