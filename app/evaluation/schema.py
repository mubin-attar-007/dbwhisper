"""The unit of evaluation: one question, and everything needed to judge the answer.

An evaluation case is a *contract*, not a question-and-answer pair. It states what the pipeline is
expected to do (answer / ask / refuse), what it is expected to touch (tables, columns, join edges),
and - for answerable cases - the reference SQL whose result set is the correctness oracle.

Three decisions in this file are worth defending:

* **Reference SQL is the oracle, not reference text.** Comparing generated SQL to gold SQL by string
  or by AST punishes a query that is right and differently phrased. The result set is what a user
  cares about, so that is what is compared (:mod:`app.evaluation.metrics`).
* **Derivable facts are derived, not typed twice.** ``expected_tables``, ``expected_columns`` and
  ``expected_relationships`` all follow from ``gold_sql``; a hand-maintained copy in YAML drifts the
  first time somebody edits a query. They are parsed out of the gold SQL on load, and the YAML may
  override them when a case wants to assert something the SQL does not show.
* **``expected_refusal`` and ``expected_clarification`` are separate fields, not one "not answerable"
  flag.** Refusing an unsafe request and asking about an ambiguous one are different behaviours with
  different failure modes, and the metrics count them separately.

A case with ``expected_behavior=ANSWER`` must carry ``gold_sql``; a case with ``CLARIFY`` must carry
``expected_clarification``; a case with ``REFUSE`` must carry ``safety_category``. ``EvalCase``
validates that on construction rather than letting a half-specified case score as a pass.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------------------------


class ExpectedBehavior(StrEnum):
    """What a correct pipeline does with this question."""

    #: Produce SQL, run it, and return rows matching the gold result set.
    ANSWER = "answer"
    #: Stop and ask a question, because a reasonable default does not exist.
    CLARIFY = "clarify"
    #: Decline: the request is unsafe, out of scope, or not answerable from this schema.
    REFUSE = "refuse"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class SafetyCategory(StrEnum):
    """Why a refusal is expected. Only meaningful on ``REFUSE`` cases."""

    WRITE_ATTEMPT = "write_attempt"
    SCHEMA_DESTRUCTION = "schema_destruction"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    SYSTEM_CATALOG = "system_catalog"
    MULTI_STATEMENT = "multi_statement"
    PROMPT_INJECTION = "prompt_injection"
    DATA_EXFILTRATION = "data_exfiltration"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    OUT_OF_SCOPE = "out_of_scope"
    SENSITIVE_DATA = "sensitive_data"


class TurnRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """One earlier turn, for cases that only make sense as a follow-up."""

    role: TurnRole
    text: str
    #: SQL the assistant ran on that turn, when the follow-up depends on it.
    sql: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role.value, "text": self.text}
        if self.sql:
            out["sql"] = self.sql
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ConversationTurn:
        return cls(
            role=TurnRole(str(raw.get("role", "user"))),
            text=str(raw.get("text", "")),
            sql=(str(raw["sql"]) if raw.get("sql") else None),
        )


# ---------------------------------------------------------------------------------------------
# The case
# ---------------------------------------------------------------------------------------------


class EvalCaseError(ValueError):
    """A case is internally inconsistent and would score misleadingly if it ran."""


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One evaluation question and the contract it is judged against."""

    id: str
    dataset: str
    database: str
    dialect: str
    question: str
    expected_behavior: ExpectedBehavior = ExpectedBehavior.ANSWER
    conversation_context: tuple[ConversationTurn, ...] = ()
    gold_sql: str | None = None
    #: Pinned hash of the gold result set. Usually ``None``: the fixture is regenerated
    #: deterministically, so the live gold execution is the oracle and a pinned copy only duplicates
    #: it. Set it on a case where you want generator drift to fail loudly.
    expected_result_hash: str | None = None
    expected_tables: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = ()
    expected_relationships: tuple[str, ...] = ()
    #: The business metric the question asks for, when it names one (``revenue``, ``churn_rate``).
    expected_metric: str | None = None
    #: Substrings a good clarifying question would contain. Any one matching counts.
    expected_clarification: tuple[str, ...] = ()
    #: Substrings a good refusal would contain. Any one matching counts.
    expected_refusal: tuple[str, ...] = ()
    safety_category: SafetyCategory | None = None
    difficulty: Difficulty = Difficulty.MEDIUM
    tags: tuple[str, ...] = ()
    notes: str = ""
    #: Answer supplied when the pipeline interrupts to ask. Lets a CLARIFY case be resumed.
    clarification_answer: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise EvalCaseError("every case needs an id")
        if self.expected_behavior is ExpectedBehavior.ANSWER and not (self.gold_sql or "").strip():
            raise EvalCaseError(f"{self.id}: an 'answer' case needs gold_sql")
        if self.expected_behavior is ExpectedBehavior.CLARIFY and not self.expected_clarification:
            raise EvalCaseError(f"{self.id}: a 'clarify' case needs expected_clarification")
        if self.expected_behavior is ExpectedBehavior.REFUSE and self.safety_category is None:
            raise EvalCaseError(f"{self.id}: a 'refuse' case needs safety_category")

    # -- derived views -------------------------------------------------------------------------
    @property
    def is_answerable(self) -> bool:
        return self.expected_behavior is ExpectedBehavior.ANSWER

    @property
    def is_conversational(self) -> bool:
        return bool(self.conversation_context)

    @property
    def tag_set(self) -> frozenset[str]:
        return frozenset(self.tags)

    def has_tag(self, tag: str) -> bool:
        return tag in self.tag_set

    def contextual_question(self) -> str:
        """The question as the pipeline sees it, with prior turns rendered in front of it.

        The v2 graph is single-turn: it takes one question and one data source. A follow-up like
        "and the month before?" is therefore evaluated by replaying the conversation into the
        question text. That is a *harness* decision and it is recorded in the report's limitations -
        it measures whether the pipeline can use written context, not whether it maintains state.
        """
        if not self.conversation_context:
            return self.question
        lines = ["Earlier in this conversation:"]
        for turn in self.conversation_context:
            label = "I asked" if turn.role is TurnRole.USER else "You answered"
            lines.append(f"- {label}: {turn.text}")
            if turn.sql:
                lines.append(f"  (the query that ran was: {turn.sql})")
        lines.append("")
        lines.append(f"Now: {self.question}")
        return "\n".join(lines)

    # -- serialisation -------------------------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "dataset": self.dataset,
            "database": self.database,
            "dialect": self.dialect,
            "question": self.question,
            "expected_behavior": self.expected_behavior.value,
            "difficulty": self.difficulty.value,
        }
        if self.conversation_context:
            out["conversation_context"] = [t.as_dict() for t in self.conversation_context]
        for key, value in (
            ("gold_sql", self.gold_sql),
            ("expected_result_hash", self.expected_result_hash),
            ("expected_metric", self.expected_metric),
            ("clarification_answer", self.clarification_answer),
            ("notes", self.notes),
        ):
            if value:
                out[key] = value
        for key, seq in (
            ("expected_tables", self.expected_tables),
            ("expected_columns", self.expected_columns),
            ("expected_relationships", self.expected_relationships),
            ("expected_clarification", self.expected_clarification),
            ("expected_refusal", self.expected_refusal),
            ("tags", self.tags),
        ):
            if seq:
                out[key] = list(seq)
        if self.safety_category is not None:
            out["safety_category"] = self.safety_category.value
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, defaults: dict[str, Any] | None = None) -> EvalCase:
        merged: dict[str, Any] = {**(defaults or {}), **raw}
        behavior = ExpectedBehavior(str(merged.get("expected_behavior", "answer")))
        safety = merged.get("safety_category")
        return cls(
            id=str(merged.get("id", "")),
            dataset=str(merged.get("dataset", "")),
            database=str(merged.get("database", "")),
            dialect=str(merged.get("dialect", "sqlite")),
            question=str(merged.get("question", "")),
            expected_behavior=behavior,
            conversation_context=tuple(
                ConversationTurn.from_dict(t) for t in (merged.get("conversation_context") or [])
            ),
            gold_sql=_clean_sql(merged.get("gold_sql")),
            expected_result_hash=_optional_str(merged.get("expected_result_hash")),
            expected_tables=_strings(merged.get("expected_tables")),
            expected_columns=_strings(merged.get("expected_columns")),
            expected_relationships=_strings(merged.get("expected_relationships")),
            expected_metric=_optional_str(merged.get("expected_metric")),
            expected_clarification=_strings(merged.get("expected_clarification")),
            expected_refusal=_strings(merged.get("expected_refusal")),
            safety_category=SafetyCategory(str(safety)) if safety else None,
            difficulty=Difficulty(str(merged.get("difficulty", "medium"))),
            tags=_strings(merged.get("tags")),
            notes=str(merged.get("notes", "")),
            clarification_answer=_optional_str(merged.get("clarification_answer")),
        )

    def with_derived_expectations(self) -> EvalCase:
        """Fill ``expected_tables`` / ``expected_columns`` / ``expected_relationships`` from gold SQL.

        Explicit YAML values always win: a case may legitimately expect retrieval to surface a table
        the reference query happens not to need.
        """
        if not self.gold_sql:
            return self
        from app.evaluation.sqlfacts import sql_facts

        facts = sql_facts(self.gold_sql, self.dialect)
        return _replace(
            self,
            expected_tables=self.expected_tables or facts.tables,
            expected_columns=self.expected_columns or facts.columns,
            expected_relationships=self.expected_relationships or facts.join_edges,
        )


def _replace(case: EvalCase, **changes: Any) -> EvalCase:
    """``dataclasses.replace`` for a slotted frozen dataclass, spelled out for clarity."""
    data = {
        field_name: getattr(case, field_name)
        for field_name in (
            "id",
            "dataset",
            "database",
            "dialect",
            "question",
            "expected_behavior",
            "conversation_context",
            "gold_sql",
            "expected_result_hash",
            "expected_tables",
            "expected_columns",
            "expected_relationships",
            "expected_metric",
            "expected_clarification",
            "expected_refusal",
            "safety_category",
            "difficulty",
            "tags",
            "notes",
            "clarification_answer",
        )
    }
    data.update(changes)
    return EvalCase(**data)


# ---------------------------------------------------------------------------------------------
# The dataset
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalDataset:
    """A named, versioned collection of cases against one generated database."""

    name: str
    version: str
    database: str
    dialect: str
    description: str
    cases: tuple[EvalCase, ...]
    #: True when the underlying data is fabricated. Never publish a healthcare number without it.
    synthetic: bool = True
    created: str = ""
    notes: str = ""

    def __len__(self) -> int:
        return len(self.cases)

    def by_id(self, case_id: str) -> EvalCase | None:
        return next((c for c in self.cases if c.id == case_id), None)

    def select(
        self,
        *,
        behaviors: Iterable[ExpectedBehavior] | None = None,
        tags: Iterable[str] | None = None,
        ids: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> tuple[EvalCase, ...]:
        wanted_behaviors = set(behaviors) if behaviors else None
        wanted_tags = set(tags) if tags else None
        wanted_ids = set(ids) if ids else None
        out = [
            case
            for case in self.cases
            if (wanted_behaviors is None or case.expected_behavior in wanted_behaviors)
            and (wanted_tags is None or (case.tag_set & wanted_tags))
            and (wanted_ids is None or case.id in wanted_ids)
        ]
        return tuple(out[:limit] if limit else out)

    def counts_by_behavior(self) -> dict[str, int]:
        counts: dict[str, int] = {b.value: 0 for b in ExpectedBehavior}
        for case in self.cases:
            counts[case.expected_behavior.value] += 1
        return counts

    def counts_by_tag(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in self.cases:
            for tag in case.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def identity(self) -> str:
        """``name@version`` - what a provenance block records as the dataset."""
        return f"{self.name}@{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.name,
            "version": self.version,
            "database": self.database,
            "dialect": self.dialect,
            "description": self.description,
            "synthetic": self.synthetic,
            "created": self.created,
            "notes": self.notes,
            "cases": [case.as_dict() for case in self.cases],
        }

    def content_hash(self) -> str:
        """Stable hash over every case, so a report can prove which corpus produced it."""
        payload = yaml.safe_dump(
            [case.as_dict() for case in self.cases], sort_keys=True, allow_unicode=True
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------
# YAML load / dump
# ---------------------------------------------------------------------------------------------


def load_dataset(path: Path | str, *, derive: bool = True) -> EvalDataset:
    """Read a case file. ``derive=False`` returns exactly what the YAML says, for round-trip tests."""
    file_path = Path(path)
    raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise EvalCaseError(f"{file_path}: expected a mapping at the top level")

    name = str(raw.get("dataset") or file_path.stem)
    defaults: dict[str, Any] = {
        "dataset": name,
        "database": str(raw.get("database") or name),
        "dialect": str(raw.get("dialect") or "sqlite"),
    }
    entries = raw.get("cases") or []
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise EvalCaseError(f"{file_path}: every case must be a mapping, got {type(entry)}")
        case = EvalCase.from_dict(entry, defaults=defaults)
        if case.id in seen:
            raise EvalCaseError(f"{file_path}: duplicate case id '{case.id}'")
        seen.add(case.id)
        cases.append(case.with_derived_expectations() if derive else case)

    return EvalDataset(
        name=name,
        version=str(raw.get("version") or "0.0.0"),
        database=defaults["database"],
        dialect=defaults["dialect"],
        description=str(raw.get("description") or ""),
        cases=tuple(cases),
        synthetic=bool(raw.get("synthetic", True)),
        created=str(raw.get("created") or ""),
        notes=str(raw.get("notes") or ""),
    )


def dump_dataset(dataset: EvalDataset, path: Path | str) -> Path:
    """Write a dataset back to YAML. Used by tooling that generates or rewrites cases."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    body = dataset.as_dict()
    file_path.write_text(
        yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8"
    )
    return file_path


# ---------------------------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------------------------


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(v) for v in value if str(v).strip())
    return ()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_sql(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if text.endswith(";"):
        text = text[:-1].strip()
    return text or None


__all__ = [
    "ConversationTurn",
    "Difficulty",
    "EvalCase",
    "EvalCaseError",
    "EvalDataset",
    "ExpectedBehavior",
    "SafetyCategory",
    "TurnRole",
    "dump_dataset",
    "load_dataset",
]
