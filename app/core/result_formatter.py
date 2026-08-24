"""Turn an execution result into the API's response payload.

The v1 response shape is preserved exactly (``results``, ``sql``, ``row_count``, ``csv``,
``raw_json``, ``describe``, ``describe_text``, pagination fields) so existing clients keep working,
but the numbers now come from :mod:`app.execution.results` rather than from a pandas ``describe()``.
That matters for honesty as much as for speed: ``describe()`` will happily report the mean of a
primary key, and a summary model reading that mean has no way to know it is meaningless.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.execution.results import ResultFrame, ResultStats, compute_stats
from app.security.export_safety import SafeCsv, to_safe_csv


def _serialize_value(value: Any) -> Any:
    """JSON-safe scalar, preserving type where JSON can carry it."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if value != value else value  # NaN -> null
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


def _records(frame: ResultFrame) -> list[dict[str, Any]]:
    return [
        {column: _serialize_value(value) for column, value in zip(frame.columns, row, strict=False)}
        for row in frame.rows
    ]


def _safe_csv(frame: ResultFrame) -> SafeCsv:
    """CSV with spreadsheet-executable cells neutralised.

    A cell beginning ``=``, ``+``, ``-``, ``@`` or a control character is a formula to Excel and
    Sheets, and these values came out of someone else's database. The neutraliser prefixes an
    apostrophe, which spreadsheets strip on display and which is reversible, so the export stays
    faithful; signed numbers are left alone so numeric columns are not corrupted.
    """
    rows = [["" if v is None else _serialize_value(v) for v in row] for row in frame.rows]
    return to_safe_csv(frame.columns, rows)


def _csv_text(frame: ResultFrame) -> str:
    return _safe_csv(frame).text


def _table_text(frame: ResultFrame) -> str:
    """A fixed-width rendering for ``output_format="table"``."""
    if not frame.columns:
        return ""
    rendered = [
        [("" if v is None else str(_serialize_value(v))) for v in row] for row in frame.rows
    ]
    widths = [
        max(len(column), *(len(r[i]) for r in rendered)) if rendered else len(column)
        for i, column in enumerate(frame.columns)
    ]
    lines = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(frame.columns)).rstrip()]
    lines.extend("  ".join(v.ljust(widths[i]) for i, v in enumerate(r)).rstrip() for r in rendered)
    return "\n".join(lines)


def _describe_map(stats: ResultStats) -> dict[str, dict[str, Any]]:
    """Per-column metrics keyed by column name (the ``describe`` field of the response)."""
    out: dict[str, dict[str, Any]] = {}
    for column in stats.columns:
        entry: dict[str, Any] = {
            "kind": column.kind,
            "count": column.non_null,
            "nulls": column.nulls,
        }
        if column.distinct is not None:
            entry["distinct"] = column.distinct
        if column.minimum is not None:
            entry["min"] = _serialize_value(column.minimum)
        if column.maximum is not None:
            entry["max"] = _serialize_value(column.maximum)
        if column.mean is not None:
            entry["mean"] = column.mean
        if column.median is not None:
            entry["median"] = column.median
        if column.total is not None:
            entry["sum"] = column.total
        if column.top_values:
            entry["top"] = {str(_serialize_value(v)): n for v, n in column.top_values}
        if column.note:
            entry["note"] = column.note
        out[column.name] = entry
    return out


def format_frame(
    frame: ResultFrame,
    sql: str,
    output_format: str = "json",
    stats: ResultStats | None = None,
) -> dict[str, Any]:
    """Build the ``QueryResultData`` payload for a successful execution."""
    stats = stats or compute_stats(frame)
    records = _records(frame)
    # Built once: the payload always carries a `csv` field, and the neutralised-cell count
    # feeds the data.exported audit event.
    exported = _safe_csv(frame)

    if output_format == "table":
        results: Any = _table_text(frame)
    elif output_format == "csv":
        results = exported.text
    else:
        results = records

    data: dict[str, Any] = {
        "results": results,
        "sql": sql,
        "row_count": frame.row_count,
        "execution_time_ms": round(frame.duration_ms, 2) if frame.duration_ms is not None else None,
        "csv": exported.text,
        "raw_json": json.dumps(records, default=str),
        "describe": _describe_map(stats),
        "describe_text": stats.describe_text(),
        "truncated": frame.truncated,
        "row_limit": frame.row_limit,
        # Non-zero means the export held cells a spreadsheet would otherwise have executed.
        "cells_neutralized": exported.cells_neutralized,
    }
    if frame.page is not None:
        data["page"] = frame.page
    if frame.page_size is not None:
        data["page_size"] = frame.page_size
    has_next = frame.has_next_page
    if has_next is not None:
        data["has_next"] = has_next
    if frame.total_rows is not None:
        data["total_rows"] = frame.total_rows

    return {"status": "success", "data": data}


__all__ = ["format_frame"]
