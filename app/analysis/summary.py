"""Grounded summaries - and a deterministic one when no model may be used.

Two rules make a summary trustworthy rather than merely fluent:

1. **The model only sees what it is allowed to cite.** The prompt carries the question, the columns,
   the deterministic statistics, a bounded row sample and the truncation flags. It never receives the
   full result set, and under a restrictive egress policy it receives no rows at all.
2. **There is always an answer without a model.** ``deterministic_summary`` describes the result from
   the statistics alone. It is what a ``LOCAL_ONLY`` deployment with no local model gets, what a
   provider outage falls back to, and what the evaluation harness compares against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.analysis.shape import ShapeReport
from app.execution.results import ResultFrame, ResultStats
from app.platform.modes import EgressPolicy

SUMMARY_PROMPT_VERSION = "grounded_summary@2.0"

#: How many rows the summary model may see, by egress policy. Schema-only means none.
SAMPLE_ROWS_BY_POLICY: dict[EgressPolicy, int] = {
    EgressPolicy.LOCAL_ONLY: 20,
    EgressPolicy.SCHEMA_ONLY_REMOTE: 0,
    EgressPolicy.MASKED_METADATA_REMOTE: 5,
    EgressPolicy.AGGREGATES_REMOTE: 0,
    EgressPolicy.REMOTE_ALLOWED: 20,
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "observations": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "caveats": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "follow_ups": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["answer", "observations"],
}


@dataclass(slots=True)
class Summary:
    answer: str
    observations: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    grounded_by: str = "deterministic"

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "observations": self.observations,
            "caveats": self.caveats,
            "follow_ups": self.follow_ups,
            "grounded_by": self.grounded_by,
        }

    def as_text(self) -> str:
        parts = [self.answer, *self.observations]
        if self.caveats:
            parts.append("Caveats: " + "; ".join(self.caveats))
        return " ".join(p.strip() for p in parts if p and p.strip())


def sample_rows_allowed(policy: EgressPolicy, *, provider_is_local: bool) -> int:
    """A local model sees the sample regardless of egress policy: nothing leaves the host."""
    if provider_is_local:
        return SAMPLE_ROWS_BY_POLICY[EgressPolicy.LOCAL_ONLY]
    return SAMPLE_ROWS_BY_POLICY.get(policy, 0)


def build_summary_prompt(
    *,
    question: str,
    sql: str,
    stats: ResultStats,
    frame: ResultFrame,
    shape: ShapeReport | None = None,
    sample_rows: int = 20,
) -> str:
    """The exact text the summary model receives. Everything in it is citable."""
    sections = [
        "QUESTION",
        question.strip(),
        "",
        "SQL THAT PRODUCED THE RESULT",
        sql.strip(),
        "",
        "COLUMN STATISTICS (computed deterministically - these numbers are authoritative)",
        stats.describe_text(),
    ]
    if sample_rows > 0 and not frame.is_empty:
        sample = frame.to_records()[:sample_rows]
        sections += [
            "",
            f"SAMPLE ROWS ({len(sample)} of {frame.row_count} returned)",
            json.dumps(sample, default=str)[:4000],
        ]
    else:
        sections += ["", "SAMPLE ROWS", "(withheld by the data-egress policy)"]
    if frame.truncated:
        sections += [
            "",
            f"NOTE: the result was capped at {frame.row_count} row(s); more rows may exist.",
        ]
    if shape and shape.warnings:
        sections += [
            "",
            "SHAPE WARNINGS",
            *[f"- {w.message}" for w in shape.warnings],
        ]
    sections += [
        "",
        "INSTRUCTIONS",
        "Summarise what this result shows for a business reader.",
        "- Only state numbers that appear above. Do not estimate, extrapolate or round misleadingly.",
        "- Do not claim a cause. Describe what the data shows, not why.",
        "- If the result is empty, say so plainly.",
        "- If a shape warning is listed, mention it in caveats.",
    ]
    return "\n".join(sections)


def deterministic_summary(
    *, question: str, frame: ResultFrame, stats: ResultStats, shape: ShapeReport | None = None
) -> Summary:
    """Describe the result from statistics alone. Never wrong, never speculative."""
    if frame.is_empty:
        return Summary(
            answer="The query ran successfully and matched no rows.",
            observations=[
                f"Columns returned: {', '.join(frame.columns)}." if frame.columns else ""
            ],
            caveats=[
                "An empty result can be the correct answer, or the filters may be too narrow."
            ],
        )

    row_word = "row" if frame.row_count == 1 else "rows"
    answer = (
        f"The query returned {frame.row_count} {row_word} across {len(frame.columns)} column(s)."
    )

    # A single scalar is the answer; say it outright.
    if frame.row_count == 1 and len(frame.columns) == 1:
        value = frame.rows[0][0]
        answer = f"{frame.columns[0]}: {value}."

    observations: list[str] = []
    for column in stats.columns:
        if column.kind == "numeric" and column.mean is not None:
            observations.append(
                f"{column.name} ranges from {column.minimum:g} to {column.maximum:g} "
                f"(mean {column.mean:.4g}, total {column.total:.6g})."
            )
        elif column.kind == "temporal" and column.minimum is not None:
            observations.append(f"{column.name} spans {column.minimum} to {column.maximum}.")
        elif column.top_values:
            top = ", ".join(f"{value} ({count})" for value, count in column.top_values[:3])
            observations.append(f"Most common {column.name}: {top}.")
        if len(observations) >= 3:
            break

    caveats: list[str] = []
    if frame.truncated:
        caveats.append(f"Only the first {frame.row_count} {row_word} are included; more may exist.")
    for column in stats.columns:
        if column.null_fraction > 0.2:
            caveats.append(f"{column.name} is {column.null_fraction:.0%} empty.")
            break
    if shape:
        caveats.extend(w.message for w in shape.warnings)

    return Summary(answer=answer, observations=observations, caveats=caveats)


def summary_from_payload(payload: dict[str, Any], *, grounded_by: str) -> Summary:
    """Convert a validated model payload into a :class:`Summary`."""
    return Summary(
        answer=str(payload.get("answer") or "").strip(),
        observations=[str(o) for o in (payload.get("observations") or [])][:4],
        caveats=[str(c) for c in (payload.get("caveats") or [])][:3],
        follow_ups=[str(f) for f in (payload.get("follow_ups") or [])][:3],
        grounded_by=grounded_by,
    )


__all__ = [
    "SAMPLE_ROWS_BY_POLICY",
    "SUMMARY_PROMPT_VERSION",
    "SUMMARY_SCHEMA",
    "Summary",
    "build_summary_prompt",
    "deterministic_summary",
    "sample_rows_allowed",
    "summary_from_payload",
]
