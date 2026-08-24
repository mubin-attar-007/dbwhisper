"""Does the result look like an answer to the question that was asked?

A query can be valid SQL, run without error, and still not answer the question - "top 5 products"
returning 500 rows, a trend with no time column, a comparison missing one of the groups. Catching
that before the summary stage is what stops a confident paragraph being written about the wrong data.

These checks are deliberately conservative. A genuinely empty table is not a failure, and a query
that legitimately returns one row is not "too few". Every finding says what was expected and what
arrived, so the UI can show it and the user can judge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.execution.results import ResultFrame, ResultStats


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"


@dataclass(slots=True)
class ShapeFinding:
    code: str
    severity: Severity
    message: str
    expected: Any = None
    actual: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(slots=True)
class ShapeReport:
    findings: list[ShapeFinding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity is Severity.WARNING for f in self.findings)

    @property
    def warnings(self) -> list[ShapeFinding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks_run": self.checks_run,
            "findings": [f.as_dict() for f in self.findings],
        }


_TOP_N = re.compile(r"\b(?:top|bottom|first|last)\s+(\d{1,4})\b", re.IGNORECASE)
_TREND_WORDS = frozenset(
    [
        "trend",
        "over",
        "time",
        "monthly",
        "weekly",
        "daily",
        "yearly",
        "quarterly",
        "per",
        "month",
        "per",
        "day",
        "growth",
        "history",
    ]
)
_COMPARISON_WORDS = frozenset(
    ["compare", "comparison", "versus", "vs", "between", "against", "difference"]
)


def requested_limit(question: str, plan_limit: int | None = None) -> int | None:
    """The row count the user asked for, from the plan if known or the question if not."""
    if plan_limit:
        return plan_limit
    match = _TOP_N.search(question or "")
    return int(match.group(1)) if match else None


def verify_shape(
    *,
    question: str,
    frame: ResultFrame,
    stats: ResultStats,
    plan_limit: int | None = None,
    expected_dimensions: list[str] | None = None,
    expected_metrics: list[str] | None = None,
) -> ShapeReport:
    """Compare what came back with what the question implies."""
    report = ShapeReport()
    lowered = (question or "").lower()
    words = set(re.findall(r"[a-z]+", lowered))
    columns_lower = [c.lower() for c in frame.columns]

    # 1. Explicit row count -----------------------------------------------------------------
    wanted = requested_limit(question, plan_limit)
    if wanted is not None:
        report.checks_run.append("row_count")
        if frame.row_count > wanted:
            report.findings.append(
                ShapeFinding(
                    "too_many_rows",
                    Severity.WARNING,
                    f"The question asked for {wanted} row(s) but {frame.row_count} came back.",
                    expected=wanted,
                    actual=frame.row_count,
                )
            )
        elif 0 < frame.row_count < wanted:
            report.findings.append(
                ShapeFinding(
                    "fewer_rows_than_requested",
                    Severity.INFO,
                    f"Only {frame.row_count} row(s) exist, fewer than the {wanted} requested.",
                    expected=wanted,
                    actual=frame.row_count,
                )
            )

    # 2. Emptiness --------------------------------------------------------------------------
    report.checks_run.append("empty")
    if frame.is_empty:
        report.findings.append(
            ShapeFinding(
                "empty_result",
                Severity.INFO,
                "The query ran successfully and matched no rows. That may be the correct answer.",
                actual=0,
            )
        )

    # 3. Trend questions need a time column --------------------------------------------------
    if words & _TREND_WORDS:
        report.checks_run.append("time_dimension")
        has_time = any(c.kind == "temporal" for c in stats.columns) or any(
            any(hint in name for hint in ("date", "month", "week", "day", "year", "time", "period"))
            for name in columns_lower
        )
        if not has_time:
            report.findings.append(
                ShapeFinding(
                    "missing_time_dimension",
                    Severity.WARNING,
                    "The question asks about change over time but the result has no time column.",
                    expected="a date/period column",
                    actual=frame.columns,
                )
            )

    # 4. Comparison questions need more than one group ---------------------------------------
    if words & _COMPARISON_WORDS:
        report.checks_run.append("comparison_groups")
        # One row with fewer than three columns cannot express a comparison between groups.
        if frame.row_count == 1 and len(frame.columns) < 3:
            report.findings.append(
                ShapeFinding(
                    "single_group_comparison",
                    Severity.WARNING,
                    "The question compares things but only one row came back.",
                    expected=">= 2 groups",
                    actual=frame.row_count,
                )
            )

    # 5. Aggregate questions should return a measure -----------------------------------------
    if words & {"how", "many", "total", "sum", "average", "count"}:
        report.checks_run.append("measure_present")
        has_measure = any(c.kind in {"numeric", "identifier"} for c in stats.columns)
        if not has_measure and not frame.is_empty:
            report.findings.append(
                ShapeFinding(
                    "no_numeric_measure",
                    Severity.WARNING,
                    "The question asks for a quantity but no numeric column came back.",
                    expected="a numeric column",
                    actual=[f"{c.name} [{c.kind}]" for c in stats.columns],
                )
            )

    # 6. Requested dimensions and metrics present --------------------------------------------
    for label, expected_names, code in (
        ("dimension", expected_dimensions or [], "missing_dimension"),
        ("metric", expected_metrics or [], "missing_metric"),
    ):
        if not expected_names:
            continue
        report.checks_run.append(f"{label}s_present")
        for name in expected_names:
            needle = name.lower().split(".")[-1]
            if not any(needle in column for column in columns_lower):
                report.findings.append(
                    ShapeFinding(
                        code,
                        Severity.WARNING,
                        f"The plan asked for the {label} '{name}' but no matching column came back.",
                        expected=name,
                        actual=frame.columns,
                    )
                )

    # 7. Truncation --------------------------------------------------------------------------
    report.checks_run.append("truncation")
    if frame.truncated:
        report.findings.append(
            ShapeFinding(
                "truncated",
                Severity.INFO,
                f"Only the first {frame.row_count} row(s) are shown; more may exist.",
                actual=frame.row_count,
            )
        )

    return report


__all__ = ["Severity", "ShapeFinding", "ShapeReport", "requested_limit", "verify_shape"]
