"""Choosing a chart from the shape of the data - with no model involved.

Chart choice is a function of column types, cardinality and row count. Asking a model to pick one
adds latency, cost and a failure mode (a chart that implies a relationship the data does not
support) in exchange for nothing. So this is rules, and the rules explain themselves: every spec
carries the ``reason`` it was chosen, which the UI shows and a reviewer can argue with.

A table is always a valid answer. When nothing charts well, that is what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.execution.results import ColumnStats, ResultFrame, ResultStats


class ChartType(StrEnum):
    TABLE = "table"
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    LINE = "line"
    AREA = "area"
    SCATTER = "scatter"
    PIE = "pie"
    SINGLE_VALUE = "single_value"


#: Above this many categories a bar chart becomes an unreadable comb.
MAX_CATEGORIES = 30
#: A pie chart is only honest for a handful of parts of one whole.
MAX_PIE_SLICES = 6


@dataclass(slots=True)
class ChartSpec:
    chart: ChartType
    reason: str
    x: str | None = None
    y: list[str] = field(default_factory=list)
    series: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chart": self.chart.value,
            "reason": self.reason,
            "x": self.x,
            "y": self.y,
            "series": self.series,
            "notes": self.notes,
        }


def _measures(stats: ResultStats) -> list[ColumnStats]:
    """Numeric columns that represent a quantity - identifiers are excluded deliberately."""
    return [c for c in stats.columns if c.kind == "numeric" and c.mean is not None]


def _temporal(stats: ResultStats) -> list[ColumnStats]:
    return [c for c in stats.columns if c.kind == "temporal"]


def _categorical(stats: ResultStats) -> list[ColumnStats]:
    return [
        c
        for c in stats.columns
        if c.kind in {"text", "boolean"}
        or (c.kind == "numeric" and c.mean is None)  # low-cardinality numeric code
    ]


def choose_chart(frame: ResultFrame, stats: ResultStats) -> ChartSpec:
    """Pick the chart the data supports, or a table when none is a good fit."""
    if frame.is_empty:
        return ChartSpec(ChartType.TABLE, "No rows to plot.")

    measures = _measures(stats)
    temporal = _temporal(stats)
    categorical = _categorical(stats)

    # A single number is a single number.
    if frame.row_count == 1 and len(frame.columns) == 1:
        return ChartSpec(
            ChartType.SINGLE_VALUE,
            "One row and one column: the answer is a single value.",
            y=[frame.columns[0]],
        )

    # Time series: a date column plus at least one measure.
    if temporal and measures:
        time_column = temporal[0]
        spec = ChartSpec(
            ChartType.LINE,
            f"'{time_column.name}' is a date and there is a measure to plot against it.",
            x=time_column.name,
            y=[m.name for m in measures[:3]],
        )
        if len(measures) == 1 and frame.row_count >= 8:
            spec.chart = ChartType.AREA
            spec.reason += " A single measure over many periods reads well as an area."
        if categorical and categorical[0].distinct and categorical[0].distinct <= 8:
            spec.series = categorical[0].name
            spec.reason += f" Split by '{categorical[0].name}'."
        return spec

    # Category + measure: a bar chart.
    if categorical and measures:
        category = categorical[0]
        distinct = category.distinct or frame.row_count
        if distinct > MAX_CATEGORIES:
            return ChartSpec(
                ChartType.TABLE,
                f"'{category.name}' has {distinct} distinct values, too many to chart legibly.",
                notes=[f"more than {MAX_CATEGORIES} categories"],
            )
        spec = ChartSpec(
            ChartType.BAR,
            f"'{category.name}' groups the rows and there is a measure to compare across them.",
            x=category.name,
            y=[m.name for m in measures[:3]],
        )
        # Long labels read better horizontally.
        if (
            category.maximum is not None
            and isinstance(category.maximum, int)
            and category.maximum > 18
        ):
            spec.chart = ChartType.HORIZONTAL_BAR
            spec.reason += " Labels are long, so the bars run horizontally."
        if (
            len(measures) == 1
            and distinct <= MAX_PIE_SLICES
            and _looks_like_parts_of_a_whole(measures[0])
        ):
            spec.notes.append(
                f"A pie chart would also work ({distinct} slices), but bars compare lengths more accurately."
            )
        return spec

    # Two measures and nothing to group by: a scatter shows the relationship.
    if len(measures) >= 2 and not categorical and frame.row_count >= 5:
        return ChartSpec(
            ChartType.SCATTER,
            "Two numeric columns with no grouping: a scatter shows how they relate.",
            x=measures[0].name,
            y=[measures[1].name],
            notes=["A scatter shows association, not causation."],
        )

    if not measures:
        return ChartSpec(
            ChartType.TABLE,
            "No numeric measure to plot; the rows themselves are the answer.",
        )

    return ChartSpec(ChartType.TABLE, "No chart type fits this shape of data well.")


def _looks_like_parts_of_a_whole(measure: ColumnStats) -> bool:
    """Only non-negative measures can honestly be drawn as slices of a whole."""
    return measure.minimum is not None and measure.minimum >= 0


__all__ = ["MAX_CATEGORIES", "MAX_PIE_SLICES", "ChartSpec", "ChartType", "choose_chart"]
