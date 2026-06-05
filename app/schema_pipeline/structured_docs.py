"""Utilities for generating structured documentation from schema YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.models import SectionContent, StructuredSchemaData
from app.schema_pipeline.minimal_text import yaml_to_minimal_text
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


def yaml_to_structured_sections(file_path: Path | str) -> dict[str, Any]:
    """Return structured sections along with minimal summary for a schema YAML.

    This function parses a schema YAML file and returns both a minimal summary
    and detailed structured sections for comprehensive documentation coverage.

    Args:
        file_path: Path to schema YAML file

    Returns:
        Dictionary with keys:
            - table_name: Name of the table
            - schema: Schema/database name
            - minimal_summary: Compact text representation
            - sections: List of SectionContent objects with structured data

    Raises:
        FileNotFoundError: If file does not exist
        yaml.YAMLError: If YAML parsing fails
    """

    path = Path(file_path) if isinstance(file_path, str) else file_path

    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")

    with open(path, encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    table_name = data.get("table_name", path.stem)
    schema_name = data.get("schema", "dbo")

    # Get minimal summary using existing function
    try:
        minimal_summary = yaml_to_minimal_text(path)
    except Exception as error:
        from app.utils.logger import sanitize_for_log

        logger.warning(
            "Failed to generate minimal summary for %s: %s",
            path,
            sanitize_for_log(str(error), max_len=500),
        )
        minimal_summary = ""

    # Build header section
    header_lines = [
        f"Table: {table_name}",
        f"Schema: {schema_name}",
        f"Type: {data.get('object_type', 'table')}",
        f"Description: {data.get('description', '').strip()}",
    ]

    # Build columns section
    columns = data.get("columns", [])
    columns_lines = []
    if columns:
        columns_lines.append("COLUMNS:")
        for column in columns:
            nullable = "NULL" if column.get("is_nullable") else "NOT NULL"
            identity = "IDENTITY" if column.get("is_identity") else ""
            columns_lines.append(
                f"- {column.get('name')}: {column.get('type')} {nullable} {identity}".strip()
            )
            columns_lines.append(f"  Desc: {column.get('description', '').strip()}")
            if column.get("keywords"):
                columns_lines.append(f"  Keywords: {', '.join(column.get('keywords', []))}")
    else:
        columns_lines.append("COLUMNS: None")

    # Build keys section (primary keys, foreign keys, indexes, unique constraints)
    keys_lines = []

    pk = data.get("primary_key", {})
    if pk:
        keys_lines.append(
            f"PRIMARY KEY: {pk.get('constraint_name')} ({', '.join(pk.get('columns', []))})"
        )

    foreign_keys = data.get("foreign_keys", [])
    if foreign_keys:
        keys_lines.append("FOREIGN KEYS:")
        for fk in foreign_keys:
            cols = ", ".join(fk.get("columns", []))
            ref_cols = ", ".join(fk.get("referenced_columns", []))
            keys_lines.append(
                f"- {fk.get('constraint_name')}: {cols} -> {fk.get('referenced_table')}({ref_cols})"
            )

    indexes = data.get("indexes", [])
    if indexes:
        keys_lines.append("INDEXES:")
        for idx in indexes:
            unique = "UNIQUE " if idx.get("is_unique") else ""
            clustered = "CLUSTERED " if idx.get("is_clustered") else ""
            cols = []
            for col in idx.get("columns", []):
                direction = "DESC" if col.get("is_descending") else "ASC"
                cols.append(f"{col.get('column')} {direction}")
            keys_lines.append(f"- {idx.get('index_name')}: {unique}{clustered}{', '.join(cols)}")

    unique_constraints = data.get("unique_constraints", [])
    if unique_constraints:
        keys_lines.append("UNIQUE CONSTRAINTS:")
        for uc in unique_constraints:
            cols = ", ".join(uc.get("columns", []))
            keys_lines.append(f"- {uc.get('constraint_name')}: {cols}")

    # Build relationships section
    relations_lines = []
    relationships = data.get("relationships", {})

    if relationships.get("outgoing"):
        relations_lines.append("OUTGOING RELATIONS:")
        for rel in relationships.get("outgoing", []):
            relations_lines.append(f"- {rel.get('to_table')} ({rel.get('relationship_type')})")

    if relationships.get("incoming"):
        relations_lines.append("INCOMING RELATIONS:")
        for rel in relationships.get("incoming", []):
            relations_lines.append(f"- {rel.get('from_table')} ({rel.get('relationship_type')})")

    if relationships.get("many_to_many"):
        relations_lines.append("MANY-TO-MANY RELATIONS:")
        for rel in relationships.get("many_to_many", []):
            relations_lines.append(
                f"- {rel.get('to_table')} via {rel.get('via_table')} ({rel.get('relationship_type')})"
            )

    # Build statistics section
    stats = data.get("statistics", {})
    stats_lines = []
    if stats:
        stats_lines.append(
            f"STATS: Columns={stats.get('total_columns')}, "
            f"Nullable={stats.get('nullable_columns')}, "
            f"Computed={stats.get('computed_columns')}, "
            f"Indexed={stats.get('indexed_columns')}"
        )

    # Construct sections list
    sections = [
        SectionContent(name="header", text="\n".join(header_lines).strip()),
        SectionContent(name="columns", text="\n".join(columns_lines).strip()),
        SectionContent(name="keys", text="\n".join(keys_lines).strip()),
        SectionContent(name="relationships", text="\n".join(relations_lines).strip()),
        SectionContent(name="stats", text="\n".join(stats_lines).strip()),
    ]

    return {
        "table_name": table_name,
        "schema": schema_name,
        "minimal_summary": minimal_summary,
        "sections": [{"name": section.name, "text": section.text} for section in sections],
    }


def yaml_to_structured_data(file_path: Path | str) -> StructuredSchemaData:
    """Convert YAML schema file to StructuredSchemaData object.

    Args:
        file_path: Path to schema YAML file

    Returns:
        StructuredSchemaData object with all sections

    Raises:
        FileNotFoundError: If file does not exist
        yaml.YAMLError: If YAML parsing fails
    """
    payload = yaml_to_structured_sections(file_path)

    sections = [SectionContent(name=s["name"], text=s["text"]) for s in payload["sections"]]

    return StructuredSchemaData(
        table_name=payload["table_name"],
        schema=payload["schema"],
        minimal_summary=payload["minimal_summary"],
        sections=sections,
    )


__all__ = ["yaml_to_structured_data", "yaml_to_structured_sections"]
