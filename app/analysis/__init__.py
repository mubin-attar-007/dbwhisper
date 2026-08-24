"""Deterministic post-execution analysis: result shape, chart choice, grounded summaries."""

from app.analysis.charts import ChartSpec, ChartType, choose_chart
from app.analysis.shape import ShapeFinding, ShapeReport, verify_shape
from app.analysis.summary import (
    SUMMARY_SCHEMA,
    Summary,
    build_summary_prompt,
    deterministic_summary,
    sample_rows_allowed,
    summary_from_payload,
)

__all__ = [
    "SUMMARY_SCHEMA",
    "ChartSpec",
    "ChartType",
    "ShapeFinding",
    "ShapeReport",
    "Summary",
    "build_summary_prompt",
    "choose_chart",
    "deterministic_summary",
    "sample_rows_allowed",
    "summary_from_payload",
    "verify_shape",
]
