"""The declarative description of an evaluation database.

One :class:`DatasetSpec` is the single source of truth for four artefacts that must never disagree:

1. the **SQLite fixture** the gold SQL runs against;
2. the **``schema_index.yaml``** the policy engine reads to decide whether a table exists - the same
   artefact enrollment writes for a real customer database, so evaluation exercises the real
   allowlist path rather than a relaxed one;
3. the **retrieval documents** (table / column / relationship / verified query) that the retriever
   indexes;
4. the **schema summary** printed in a report so a reader knows what the questions were about.

Generating all four from one object is what stops the classic evaluation bug where the fixture has a
column the schema index does not, and every case fails for a reason that has nothing to do with the
model.

Row generation is a function of a seeded :class:`random.Random` and nothing else - no clock, no
``uuid4``, no set iteration order - so the same checkout produces the same bytes on every machine.
:func:`app.evaluation.datasets.build.fixture_digest` is the check that this stays true.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from random import Random
from typing import Any

#: Rows for one table, in insertion order. Column order matches the table's column order.
TableRows = list[tuple[Any, ...]]
#: ``table name -> rows``. Insertion order of the mapping is the order tables are populated.
Population = dict[str, TableRows]


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    sql_type: str
    nullable: bool = False
    description: str = ""

    def ddl(self) -> str:
        return f"{self.name} {self.sql_type}" + ("" if self.nullable else " NOT NULL")


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def ddl(self) -> str:
        parts = [c.ddl() for c in self.columns]
        if self.primary_key:
            parts.append(f"PRIMARY KEY ({', '.join(self.primary_key)})")
        body = ",\n    ".join(parts)
        return f"CREATE TABLE {self.name} (\n    {body}\n)"

    def insert(self) -> str:
        placeholders = ", ".join("?" for _ in self.columns)
        return f"INSERT INTO {self.name} VALUES ({placeholders})"


@dataclass(frozen=True, slots=True)
class Relationship:
    """A declared join edge. Committed in the spec rather than inferred, so retrieval can use it."""

    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]

    def label(self) -> str:
        left = f"{self.from_table}.{'/'.join(self.from_columns)}"
        right = f"{self.to_table}.{'/'.join(self.to_columns)}"
        return f"{left} -> {right}"


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Everything about one evaluation database except the questions asked of it."""

    #: Short name used in case files and reports (``retail``).
    name: str
    #: The ``db_flag`` the pipeline knows it by. Prefixed so it cannot collide with a real database.
    source_id: str
    version: str
    description: str
    domain: str
    tables: tuple[Table, ...]
    relationships: tuple[Relationship, ...]
    populate: Callable[[Random], Population]
    seed: int
    #: SQLite is the fixture engine: it is a file, it needs no server, and the whole corpus can be
    #: rebuilt in under a second on a laptop with no credentials anywhere.
    dialect: str = "sqlite"
    schema_name: str = "main"
    #: Human-approved question/SQL pairs seeded into retrieval, as a real deployment would have.
    verified_queries: tuple[tuple[str, str], ...] = ()
    #: True when the data is fabricated. Healthcare-shaped data must say so everywhere it appears.
    synthetic: bool = True
    #: Printed in the report and in the generator docstring; the place to be explicit about limits.
    provenance_note: str = ""
    covered_concepts: tuple[str, ...] = field(default_factory=tuple)

    def table(self, name: str) -> Table:
        for candidate in self.tables:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.name} has no table '{name}'")

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tables)

    def columns_by_table(self) -> dict[str, tuple[str, ...]]:
        return {t.name: t.column_names for t in self.tables}

    def qualified_columns(self) -> frozenset[str]:
        """``table.column`` for every column, lower-cased - the hallucination allowlist."""
        return frozenset(
            f"{t.name.lower()}.{c.lower()}" for t in self.tables for c in t.column_names
        )

    def relationship_labels(self) -> tuple[str, ...]:
        return tuple(sorted(r.label() for r in self.relationships))

    def new_random(self) -> Random:
        return Random(self.seed)

    def generate(self) -> Population:
        """Produce every row. Deterministic by construction: the only entropy is ``self.seed``."""
        population = self.populate(self.new_random())
        declared = set(self.table_names)
        produced = set(population)
        if declared != produced:
            missing = ", ".join(sorted(declared - produced)) or "none"
            extra = ", ".join(sorted(produced - declared)) or "none"
            raise ValueError(
                f"{self.name}: population does not match the declared tables "
                f"(missing: {missing}; unexpected: {extra})"
            )
        for name, rows in population.items():
            width = len(self.table(name).columns)
            for index, row in enumerate(rows):
                if len(row) != width:
                    raise ValueError(
                        f"{self.name}.{name} row {index} has {len(row)} values, expected {width}"
                    )
        return population

    def schema_index(self) -> dict[str, Any]:
        """The enrollment artefact, in the shape ``app/sqlpolicy/scope_loader.py`` expects."""
        return {
            "database_name": self.source_id,
            # A fixed string, not a timestamp: this file is regenerated on every build and a clock
            # value would make the fixture non-deterministic and the git diff meaningless.
            "extraction_date": f"generated:{self.name}@{self.version}",
            "total_schemas": 1,
            "total_tables": len(self.tables),
            "total_views": 0,
            "generated_by": "app.evaluation.datasets - synthetic evaluation fixture",
            "synthetic": self.synthetic,
            "schemas": [
                {"name": self.schema_name, "table_count": len(self.tables), "view_count": 0}
            ],
            "tables": [
                {
                    "table": table.name,
                    "schema": self.schema_name,
                    "object_type": "table",
                    "keywords": list(table.keywords),
                    "column_names": list(table.column_names),
                    "primary_key": list(table.primary_key),
                    "has_foreign_keys": any(r.from_table == table.name for r in self.relationships),
                    "short_description": table.description,
                }
                for table in self.tables
            ],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_id": self.source_id,
            "version": self.version,
            "domain": self.domain,
            "dialect": self.dialect,
            "synthetic": self.synthetic,
            "tables": len(self.tables),
            "relationships": len(self.relationships),
            "table_names": list(self.table_names),
            "covered_concepts": list(self.covered_concepts),
            "provenance_note": self.provenance_note,
        }


# ---------------------------------------------------------------------------------------------
# Deterministic value helpers
# ---------------------------------------------------------------------------------------------


def date_series(start: str, days: int) -> list[str]:
    """``days`` consecutive ISO dates from ``start``. Text dates, because SQLite has no date type."""
    from datetime import date, timedelta

    year, month, day = (int(p) for p in start.split("-"))
    first = date(year, month, day)
    return [(first + timedelta(days=offset)).isoformat() for offset in range(days)]


def weighted_choice(rng: Random, options: Sequence[tuple[Any, float]]) -> Any:
    """Pick with weights using only ``rng.random()``, so results depend on nothing else."""
    total = sum(weight for _, weight in options)
    threshold = rng.random() * total
    running = 0.0
    for value, weight in options:
        running += weight
        if threshold <= running:
            return value
    return options[-1][0]


def money(value: float) -> float:
    """Round to cents. Money that carries float noise makes every SUM comparison a coin toss."""
    return round(value + 1e-9, 2)


def code(prefix: str, number: int, width: int = 6) -> str:
    """A synthetic identifier that cannot be mistaken for a real-world one."""
    return f"{prefix}-{number:0{width}d}"


__all__ = [
    "Column",
    "DatasetSpec",
    "Population",
    "Relationship",
    "Table",
    "TableRows",
    "code",
    "date_series",
    "money",
    "weighted_choice",
]
