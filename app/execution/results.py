"""Result frames and the deterministic statistics computed from them.

Everything here is arithmetic, not inference: the summary a model later writes has to be grounded in
numbers something else calculated. Two rules keep those numbers honest:

* **Do not average an identifier.** Summing customer ids produces a number that means nothing, so
  columns that look like keys, codes or flags are described by cardinality instead of by mean.
* **Say when the answer is partial.** A frame that hit the row cap is marked ``truncated``; every
  statistic derived from it describes the rows returned, not the rows that exist.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

ColumnKind = Literal["numeric", "temporal", "boolean", "text", "identifier", "other"]

_ID_NAME = re.compile(r"(^|_)(id|uuid|guid|key|code|no|num|number|ref|hash|sku|isbn)s?$", re.I)
_ID_PREFIX = re.compile(r"^(id|uuid|guid|pk|fk)(_|$)", re.I)
#: Below this many distinct values a numeric column is treated as a category, not a measure.
_LOW_CARDINALITY = 12


@dataclass(slots=True)
class ResultFrame:
    """Rows returned by one query, plus everything the caller needs to describe them honestly."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool = False
    row_limit: int | None = None
    duration_ms: float | None = None
    total_rows: int | None = None
    page: int | None = None
    page_size: int | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def is_empty(self) -> bool:
        return not self.rows

    @property
    def has_next_page(self) -> bool | None:
        """Whether another page exists, when the caller asked for one. ``None`` if unknown."""
        if self.page is None or self.page_size is None:
            return None
        if self.total_rows is not None:
            return (self.page * self.page_size) < self.total_rows
        return self.truncated

    def column_values(self, name: str) -> list[Any]:
        index = self.columns.index(name)
        return [row[index] for row in self.rows]

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]


@dataclass(slots=True)
class ColumnStats:
    name: str
    kind: ColumnKind
    non_null: int
    nulls: int
    distinct: int | None = None
    minimum: Any = None
    maximum: Any = None
    mean: float | None = None
    median: float | None = None
    total: float | None = None
    top_values: list[tuple[Any, int]] = field(default_factory=list)
    note: str | None = None

    @property
    def null_fraction(self) -> float:
        total = self.non_null + self.nulls
        return (self.nulls / total) if total else 0.0


@dataclass(slots=True)
class ResultStats:
    row_count: int
    column_count: int
    truncated: bool
    columns: list[ColumnStats]
    duration_ms: float | None = None

    def by_name(self, name: str) -> ColumnStats | None:
        return next((c for c in self.columns if c.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "columns": [
                {
                    "name": c.name,
                    "kind": c.kind,
                    "non_null": c.non_null,
                    "nulls": c.nulls,
                    "null_fraction": round(c.null_fraction, 4),
                    "distinct": c.distinct,
                    "min": _plain(c.minimum),
                    "max": _plain(c.maximum),
                    "mean": c.mean,
                    "median": c.median,
                    "sum": c.total,
                    "top_values": [[_plain(v), n] for v, n in c.top_values],
                    "note": c.note,
                }
                for c in self.columns
            ],
        }

    def describe_text(self) -> str:
        """A compact, model-friendly rendering. This is what a summary prompt is allowed to see."""
        lines = [
            f"rows={self.row_count} columns={self.column_count} truncated={str(self.truncated).lower()}"
        ]
        for c in self.columns:
            parts = [f"{c.name} [{c.kind}]", f"non_null={c.non_null}", f"nulls={c.nulls}"]
            if c.distinct is not None:
                parts.append(f"distinct={c.distinct}")
            if c.minimum is not None:
                parts.append(f"min={_plain(c.minimum)}")
            if c.maximum is not None:
                parts.append(f"max={_plain(c.maximum)}")
            if c.mean is not None:
                parts.append(f"mean={c.mean:.4g}")
            if c.median is not None:
                parts.append(f"median={c.median:.4g}")
            if c.total is not None:
                parts.append(f"sum={c.total:.6g}")
            if c.top_values:
                rendered = ", ".join(f"{_plain(v)}={n}" for v, n in c.top_values)
                parts.append(f"top=({rendered})")
            if c.note:
                parts.append(f"note={c.note}")
            lines.append("  " + " ".join(parts))
        return "\n".join(lines)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _looks_like_identifier(name: str, values: Sequence[Any]) -> bool:
    if _ID_NAME.search(name) or _ID_PREFIX.match(name):
        return True
    # An integer column whose values are all distinct and strictly increasing is a surrogate key.
    if len(values) >= 5 and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return len(set(values)) == len(values)
    return False


def _classify(name: str, values: Sequence[Any]) -> ColumnKind:
    if not values:
        return "other"
    if all(isinstance(v, bool) for v in values):
        return "boolean"
    if all(isinstance(v, (datetime, date, time)) for v in values):
        return "temporal"
    if all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in values):
        return "identifier" if _looks_like_identifier(name, values) else "numeric"
    if all(isinstance(v, str) for v in values):
        return "identifier" if _ID_NAME.search(name) or _ID_PREFIX.match(name) else "text"
    return "other"


def _top_values(values: Sequence[Any], limit: int = 5) -> list[tuple[Any, int]]:
    counts: dict[Any, int] = {}
    for value in values:
        try:
            counts[value] = counts.get(value, 0) + 1
        except TypeError:  # unhashable (dict/list column)
            return []
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return ordered[:limit]


def compute_stats(frame: ResultFrame) -> ResultStats:
    """Deterministic per-column statistics for a result frame."""
    columns: list[ColumnStats] = []
    for index, name in enumerate(frame.columns):
        raw = [row[index] for row in frame.rows]
        present = [v for v in raw if v is not None]
        kind = _classify(name, present)
        stats = ColumnStats(
            name=name,
            kind=kind,
            non_null=len(present),
            nulls=len(raw) - len(present),
        )
        try:
            stats.distinct = len(set(present))
        except TypeError:
            stats.distinct = None

        if kind == "numeric" and present:
            numbers = [float(v) for v in present]
            distinct = stats.distinct if stats.distinct is not None else len(set(numbers))
            stats.minimum, stats.maximum = min(numbers), max(numbers)
            if (
                distinct is not None
                and distinct <= _LOW_CARDINALITY
                and len(numbers) >= max(6, 2 * distinct)
            ):
                # Values repeat often enough that this is a category encoded as a number (status
                # codes, star ratings), not a measure. Three rows of a COUNT(*) is not that: the
                # threshold keeps a small aggregate result from being mistaken for a code.
                stats.top_values = _top_values(present)
                stats.note = "low-cardinality numeric; treated as categorical"
            else:
                stats.mean = statistics.fmean(numbers)
                stats.median = statistics.median(numbers)
                stats.total = sum(numbers)
        elif kind == "temporal" and present:
            stats.minimum, stats.maximum = min(present), max(present)
        elif kind == "boolean" and present:
            true_count = sum(1 for v in present if v)
            stats.top_values = [(True, true_count), (False, len(present) - true_count)]
        elif kind == "identifier":
            stats.note = "identifier column; aggregates are not meaningful"
            if present and stats.distinct == len(present):
                stats.note = "identifier column; all values distinct"
        elif kind == "text" and present:
            if stats.distinct is not None and stats.distinct <= _LOW_CARDINALITY:
                stats.top_values = _top_values(present)
            lengths = [len(v) for v in present]
            stats.minimum, stats.maximum = min(lengths), max(lengths)
            stats.note = "min/max are string lengths"
        columns.append(stats)

    return ResultStats(
        row_count=frame.row_count,
        column_count=len(frame.columns),
        truncated=frame.truncated,
        columns=columns,
        duration_ms=frame.duration_ms,
    )


__all__ = ["ColumnKind", "ColumnStats", "ResultFrame", "ResultStats", "compute_stats"]
