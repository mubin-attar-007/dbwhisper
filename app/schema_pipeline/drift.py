"""Detecting that a schema changed under a verified query.

A reviewer approving a question -> SQL pair is approving it *against a schema*. When a column is
renamed, retyped or dropped, that approval silently stops meaning anything - and the pair keeps being
handed to the model as a verified example. This module is the part that notices.

It deliberately compares **artefacts, not databases**: enrollment writes one YAML file per table, so
diffing the extracted files before and after an extraction tells us what changed without a second
round-trip to the customer's database and without needing write access to anything.

The unit of comparison is a per-table fingerprint over the column set - name, type and nullability -
rather than the full column list, because the result is persisted in a job stage row and displayed.
A fingerprint is small, and "did this table change" is the only question a drift check needs to ask.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Column attributes that change what a query means. A description edit is not drift.
SIGNIFICANT_COLUMN_KEYS = ("name", "type", "is_nullable")


def schema_dir(source_id: str, root: Path | None = None) -> Path:
    return (root or (PROJECT_ROOT / "database_schemas")) / source_id / "schema"


def _table_fingerprint(doc: dict[str, Any]) -> str:
    """A digest of the parts of a table definition that can invalidate a query."""
    parts: list[str] = []
    for column in doc.get("columns") or []:
        if not isinstance(column, dict):
            continue
        parts.append(
            "|".join(str(column.get(key, "")).strip().lower() for key in SIGNIFICANT_COLUMN_KEYS)
        )
    # Sorted: column *order* changing is not a semantic change for a query that names its columns.
    payload = ";".join(sorted(parts))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _qualified_name(doc: dict[str, Any], path: Path) -> str:
    table = str(doc.get("table_name") or path.stem).strip()
    schema = str(doc.get("schema") or "").strip()
    return f"{schema}.{table}".lower() if schema else table.lower()


def fingerprint_schema(source_id: str, root: Path | None = None) -> dict[str, str]:
    """Map ``schema.table`` -> fingerprint for every extracted table of a source.

    Returns an empty map when nothing has been extracted yet, which the diff reads as "no baseline"
    rather than as "everything was dropped".
    """
    directory = schema_dir(source_id, root)
    if not directory.is_dir():
        return {}

    fingerprints: dict[str, str] = {}
    for path in sorted(directory.rglob("*.yaml")):
        if path.name == "schema_index.yaml":
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:  # a malformed artefact must not abort a job
            logger.warning("Skipping unreadable schema artefact %s: %s", path.name, exc)
            continue
        if isinstance(doc, dict) and doc.get("columns") is not None:
            fingerprints[_qualified_name(doc, path)] = _table_fingerprint(doc)
    return fingerprints


@dataclass(slots=True)
class DriftReport:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    @property
    def affected_tables(self) -> list[str]:
        """Tables whose definition no longer matches what a reviewer approved against.

        An *added* table cannot invalidate an existing query, so it is reported but not acted on.
        """
        return sorted(set(self.removed) | set(self.changed))

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def describe(self) -> str:
        if not self.has_drift:
            return "no schema change detected"
        bits = [
            f"{len(self.changed)} changed" if self.changed else "",
            f"{len(self.removed)} removed" if self.removed else "",
            f"{len(self.added)} added" if self.added else "",
        ]
        detail = ", ".join(b for b in bits if b)
        sample = ", ".join(self.affected_tables[:5]) or ", ".join(self.added[:5])
        return f"{detail} ({sample}{'...' if len(self.affected_tables) > 5 else ''})"

    def as_dict(self) -> dict[str, Any]:
        return {"added": self.added, "removed": self.removed, "changed": self.changed}


def diff(before: dict[str, str], after: dict[str, str]) -> DriftReport:
    """Compare two fingerprint maps.

    With no baseline (``before`` empty) nothing is reported as drift: a first enrollment has not
    changed anything, and calling every table "new" would mark every verified pair stale on day one.
    """
    if not before:
        return DriftReport()
    return DriftReport(
        added=sorted(set(after) - set(before)),
        removed=sorted(set(before) - set(after)),
        changed=sorted(t for t in set(before) & set(after) if before[t] != after[t]),
    )


def apply_to_verified_queries(source_id: str, report: DriftReport) -> int:
    """Move verified pairs that touch a changed table out of circulation. Returns how many."""
    if not report.affected_tables:
        return 0
    from db.verified_queries import mark_stale_for_tables

    reason = f"Schema changed during enrollment: {report.describe()}"
    return mark_stale_for_tables(source_id, report.affected_tables, reason)


__all__ = [
    "DriftReport",
    "apply_to_verified_queries",
    "diff",
    "fingerprint_schema",
    "schema_dir",
]
