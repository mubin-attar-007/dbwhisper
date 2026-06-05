"""Utilities for generating compact textual views of schema YAML artifacts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import yaml


def yaml_to_minimal_text(file_path: Path | str) -> str:
    """Return a compact textual representation of a table definition in YAML."""

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Failed parsing YAML for {path}: {error}") from error

    parts: list[str] = []
    table_name = data.get("table_name") or path.stem
    description = data.get("description", "")
    parts.append(f"Table:{table_name}")
    parts.append(f"Desc:{description}")

    columns = _format_columns(data.get("columns", []))
    if columns:
        parts.append(f"Columns:{';'.join(columns)}")

    pk = data.get("primary_key", {})
    if pk and isinstance(pk, dict):
        columns_list: Sequence[str] = pk.get("columns", [])
        if columns_list:
            parts.append(f"PK:{','.join(columns_list)}")

    fks = _format_foreign_keys(data.get("foreign_keys", []))
    if fks:
        parts.append(f"FKs:{';'.join(fks)}")

    indexes = _format_indexes(data.get("indexes", []))
    if indexes:
        parts.append(f"Indexes:{';'.join(indexes)}")

    return "|".join(parts)


def _format_columns(columns: Sequence[dict]) -> list[str]:
    formatted: list[str] = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        name = column.get("name")
        data_type = column.get("type")
        if not name or not data_type:
            continue
        col_info = f"{name}({data_type})"
        if column.get("is_nullable"):
            col_info += ":NULL"
        formatted.append(col_info)
    return formatted


def _format_foreign_keys(foreign_keys: Sequence[dict]) -> list[str]:
    formatted: list[str] = []
    for fk in foreign_keys:
        if not isinstance(fk, dict):
            continue
        columns = fk.get("columns") or []
        referenced_table = fk.get("referenced_table")
        if not columns or not referenced_table:
            continue
        formatted.append(f"{','.join(columns)}->{referenced_table}")
    return formatted


def _format_indexes(indexes: Sequence[dict]) -> list[str]:
    formatted: list[str] = []
    for idx in indexes:
        if not isinstance(idx, dict):
            continue
        cols = [col.get("column") for col in idx.get("columns", []) if isinstance(col, dict)]
        cols = [col for col in cols if col]
        index_name = idx.get("index_name")
        if not index_name or not cols:
            continue
        formatted.append(f"{index_name}({','.join(cols)})")
    return formatted
