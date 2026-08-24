"""Build a :class:`SchemaScope` from the enrolled schema artefacts.

Until snapshots live in the catalog tables (Phase 3), the source of truth for an enrolled database is
``database_schemas/<db_flag>/schema/schema_index.yaml`` (tables, schemas, column names). The loader
is cached on the file's mtime so repeated validations do not re-read YAML.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from app.sqlpolicy.types import Dialect, SchemaScope
from app.user_db_config_loader import PROJECT_ROOT


def schema_index_path(db_flag: str) -> Path:
    return Path(PROJECT_ROOT) / "database_schemas" / db_flag / "schema" / "schema_index.yaml"


@lru_cache(maxsize=64)
def _load(path: str, mtime_ns: int, dialect: Dialect) -> SchemaScope | None:
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    tables: dict[str, dict[str, set[str]]] = {}
    for entry in data.get("tables") or []:
        name = str(entry.get("table") or "").strip()
        if not name:
            continue
        schema = str(entry.get("schema") or "").strip().lower()
        cols = {str(c).lower() for c in (entry.get("column_names") or []) if c}
        tables.setdefault(schema, {})[name.lower()] = cols
    if not tables:
        return None
    schemas = [s for s in tables if s]
    default_schema = schemas[0] if len(schemas) == 1 else (dialect.default_schema or None)
    if default_schema and default_schema not in tables and len(schemas) == 1:
        default_schema = schemas[0]
    return SchemaScope(
        dialect=dialect,
        tables=tables,
        default_schema=default_schema,
        snapshot_id=f"yaml:{p.parent.parent.name}:{mtime_ns}",
        source_id=p.parent.parent.name,
    )


def scope_from_schema_index(db_flag: str, dialect: Dialect = Dialect.GENERIC) -> SchemaScope | None:
    """Return the scope for an enrolled ``db_flag`` or ``None`` when no index exists."""
    if not db_flag or any(part in {"..", ""} for part in Path(db_flag).parts) or os.sep in db_flag:
        return None
    path = schema_index_path(db_flag)
    if not path.is_file():
        return None
    return _load(str(path), path.stat().st_mtime_ns, dialect)


__all__ = ["schema_index_path", "scope_from_schema_index"]
