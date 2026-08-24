"""Building the schema context a prompt actually receives, under a token budget.

Two failure modes shape this. Pasting every column of every candidate table blows the context window
and buries the relevant column in noise; passing only table names leaves the model guessing at column
names, which is how hallucinated columns happen. So the pack is *two-level*: every selected table
contributes its identity, keys and description, and the remaining budget is spent on the columns and
relationships most likely to matter.

The budget is enforced by construction rather than by truncating the final string, because cutting a
prompt in half mid-table produces a context that looks complete and is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.retrieval.base import DocumentKind, MatchType, RetrievalHit

#: Rough token estimate. Deliberately conservative: over-estimating wastes a little context,
#: under-estimating overflows it. Real counts come from the model layer when it reports usage.
CHARS_PER_TOKEN = 3.6
DEFAULT_TOKEN_BUDGET = 3000


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN)) if text else 0


@dataclass(slots=True)
class TableCard:
    """Everything the prompt says about one table."""

    name: str
    description: str = ""
    columns: list[str] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    reason: str = ""

    def render(self, max_columns: int | None = None) -> str:
        lines = [f"TABLE {self.name}"]
        if self.description:
            lines.append(f"  purpose: {_one_line(self.description)}")
        columns = self.columns if max_columns is None else self.columns[:max_columns]
        if columns:
            suffix = ""
            if max_columns is not None and len(self.columns) > max_columns:
                suffix = f" (+{len(self.columns) - max_columns} more)"
            lines.append(f"  columns: {', '.join(columns)}{suffix}")
        if self.primary_key:
            lines.append(f"  primary key: {', '.join(self.primary_key)}")
        for relationship in self.relationships:
            lines.append(f"  joins: {relationship}")
        return "\n".join(lines)


@dataclass(slots=True)
class ContextPack:
    tables: list[TableCard] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    glossary: list[str] = field(default_factory=list)
    verified_examples: list[tuple[str, str]] = field(default_factory=list)
    #: Ids of the pairs actually rendered above - what a usage counter should credit. A pair
    #: that ranked but was dropped for budget was never shown to the model and is not counted.
    example_pair_ids: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    token_estimate: int = 0
    budget: int = DEFAULT_TOKEN_BUDGET

    def render(self) -> str:
        """The delimited block handed to the model. Untrusted text stays inside the markers."""
        sections: list[str] = []
        if self.tables:
            sections.append("SCHEMA\n" + "\n".join(card.render() for card in self.tables))
        if self.metrics:
            sections.append("APPROVED METRICS\n" + "\n".join(f"- {m}" for m in self.metrics))
        if self.glossary:
            sections.append("BUSINESS TERMS\n" + "\n".join(f"- {g}" for g in self.glossary))
        if self.verified_examples:
            rendered = "\n\n".join(f"Q: {q}\nSQL: {sql}" for q, sql in self.verified_examples)
            sections.append("VERIFIED EXAMPLES (human-approved for this database)\n" + rendered)
        body = "\n\n".join(sections)
        if self.dropped:
            body += (
                f"\n\nNOTE: {len(self.dropped)} additional item(s) were omitted to fit the context "
                "budget. Ask a narrower question if the answer needs them."
            )
        return body

    @property
    def table_names(self) -> list[str]:
        return [card.name for card in self.tables]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tables": self.table_names,
            "metrics": self.metrics,
            "glossary_terms": len(self.glossary),
            "verified_examples": len(self.verified_examples),
            "verified_example_ids": self.example_pair_ids,
            "dropped": self.dropped,
            "token_estimate": self.token_estimate,
            "budget": self.budget,
        }


def _one_line(text: str, limit: int = 220) -> str:
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    return collapsed[:limit] + ("..." if len(collapsed) > limit else "")


def _match_reason(hit: RetrievalHit) -> str:
    return {
        MatchType.EXACT: "you named it",
        MatchType.LEXICAL: "keyword match",
        MatchType.VECTOR: "semantic match",
        MatchType.FUSED: "keyword and semantic match",
        MatchType.NEIGHBOUR: "needed to join the selected tables",
    }.get(hit.match_type, "")


def build_context_pack(
    hits: list[RetrievalHit],
    *,
    budget_tokens: int = DEFAULT_TOKEN_BUDGET,
    max_columns_per_table: int = 40,
    max_verified_examples: int = 3,
) -> ContextPack:
    """Assemble the pack, spending the budget on tables first and extras with what remains."""
    pack = ContextPack(budget=budget_tokens)
    used = 0

    cards: dict[str, TableCard] = {}
    order: list[str] = []
    metrics: list[str] = []
    glossary: list[str] = []
    examples: list[tuple[str, str, str]] = []

    for hit in hits:
        document = hit.document
        if document.kind is DocumentKind.TABLE:
            name = document.qualified_table or document.title
            if name not in cards:
                cards[name] = TableCard(name=name, reason=_match_reason(hit))
                order.append(name)
            # Merge rather than skip: a card may already exist from a column hit, holding only the
            # one column that matched. The table document is what supplies the rest.
            card = cards[name]
            if not card.description:
                card.description = document.text
            if not card.primary_key:
                card.primary_key = list(document.metadata.get("primary_key") or [])
            for column in document.metadata.get("columns") or []:
                if column not in card.columns:
                    card.columns.append(column)
        elif document.kind is DocumentKind.COLUMN:
            name = document.qualified_table or ""
            if name and name not in cards:
                # The column ranked but its table did not. Describing the column without its table
                # is how a model ends up inventing which table to select it from.
                cards[name] = TableCard(name=name, reason="a column of it matched")
                order.append(name)
            card = cards.get(name)
            if card is not None and document.column and document.column not in card.columns:
                card.columns.append(document.column)
        elif document.kind is DocumentKind.RELATIONSHIP:
            for side in (document.metadata.get("from_table"), document.metadata.get("to_table")):
                card = cards.get(str(side))
                if card is not None and document.text not in card.relationships:
                    card.relationships.append(_one_line(document.text, 120))
        elif document.kind is DocumentKind.METRIC:
            metrics.append(f"{document.title}: {_one_line(document.text)}")
        elif document.kind is DocumentKind.GLOSSARY:
            glossary.append(f"{document.title}: {_one_line(document.text)}")
        elif document.kind is DocumentKind.VERIFIED_QUERY:
            question = str(document.metadata.get("question") or document.title)
            sql = str(document.metadata.get("sql") or "")
            if sql:
                examples.append((question, sql, str(document.metadata.get("pair_id") or "")))

    # Tables first: without them nothing else can be used.
    for name in order:
        card = cards[name]
        cost = estimate_tokens(card.render(max_columns=max_columns_per_table))
        if used + cost > budget_tokens and pack.tables:
            pack.dropped.append(f"table {name}")
            continue
        card.columns = card.columns[:max_columns_per_table]
        pack.tables.append(card)
        used += cost

    for label, items, target in (
        ("metric", metrics, pack.metrics),
        ("term", glossary, pack.glossary),
    ):
        for item in items:
            cost = estimate_tokens(item)
            if used + cost > budget_tokens:
                pack.dropped.append(f"{label} {item.split(':')[0]}")
                continue
            target.append(item)
            used += cost

    for question, sql, pair_id in examples[:max_verified_examples]:
        cost = estimate_tokens(question) + estimate_tokens(sql)
        if used + cost > budget_tokens:
            pack.dropped.append(f"example {_one_line(question, 40)}")
            continue
        pack.verified_examples.append((question, sql))
        if pair_id:
            pack.example_pair_ids.append(pair_id)
        used += cost

    pack.token_estimate = used
    return pack


__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_TOKEN_BUDGET",
    "ContextPack",
    "TableCard",
    "build_context_pack",
    "estimate_tokens",
]
