"""Low-level SQL Server metadata extraction using SQLAlchemy Inspector (preferred way)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Engine, Inspector

from app.models import RawMetadata
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


class SQLServerMetadataExtractor:
    """Extracts comprehensive metadata using SQLAlchemy Inspector (no raw SQL where possible)."""

    DEFAULT_EXCLUDE_SCHEMAS: Sequence[str] = (
        "sys",
        "INFORMATION_SCHEMA",
        "guest",
        "db_owner",
        "db_accessadmin",
        "db_securityadmin",
        "db_ddladmin",
        "db_backupoperator",
        "db_datareader",
        "db_datawriter",
        "db_denydatareader",
        "db_denydatawriter",
        # Cross-dialect system schemas (Postgres / MySQL / SQLite)
        "pg_catalog",
        "pg_toast",
        "performance_schema",
        "temp",
    )

    def __init__(
        self,
        connection_string: str,
        include_schemas: Iterable[str] | None = None,
        exclude_schemas: Iterable[str] | None = None,
    ) -> None:
        self.connection_string = self._normalize_connection_string(connection_string)
        self.include_schemas = {s.lower() for s in include_schemas or []}
        base_excludes = {s.lower() for s in self.DEFAULT_EXCLUDE_SCHEMAS}
        extra_excludes = {s.lower() for s in (exclude_schemas or [])}
        self.exclude_schemas = base_excludes | extra_excludes

        logger.debug(
            "Initialised SQLServerMetadataExtractor include=%s exclude=%s",
            self.include_schemas,
            self.exclude_schemas,
        )

        self.engine: Engine = create_engine(self.connection_string, pool_pre_ping=True)
        self.inspector: Inspector = inspect(self.engine)

    def extract(self) -> RawMetadata:
        """Extract all metadata using SQLAlchemy reflection (dialect-agnostic)."""
        database_name = self._resolve_database_name()

        logger.info("Extracting metadata from database '%s' using Inspector", database_name)

        # Reflect all schemas first
        all_schemas = self.inspector.get_schema_names()
        target_schemas = [
            s
            for s in all_schemas
            if s.lower() not in self.exclude_schemas
            and (not self.include_schemas or s.lower() in self.include_schemas)
        ]

        # Use MetaData.reflect() for tables + views + constraints (fastest & most accurate)
        metadata = MetaData()
        for schema in target_schemas:
            metadata.reflect(
                bind=self.engine,
                schema=schema,
                views=True,
                only=lambda name, type_: True,  # reflect all
            )

        rows = {
            "schemas": self._get_schemas(target_schemas),
            "tables": self._get_tables(metadata, target_schemas),
            "columns": self._get_columns(metadata, target_schemas),
            "primary_keys": self._get_primary_keys(metadata, target_schemas),
            "foreign_keys": self._get_foreign_keys(metadata, target_schemas),
            "indexes": self._get_indexes(metadata, target_schemas),
            "unique_constraints": self._get_unique_constraints(metadata, target_schemas),
            "check_constraints": self._get_check_constraints(metadata, target_schemas),
            # views and view_columns intentionally removed — the pipeline focuses on tables
        }

        logger.info("Extraction complete: %d tables", len(rows["tables"]))

        return RawMetadata(database_name=database_name, **rows)

    # ------------------------------------------------------------------
    # Individual extractors using Inspector + reflected MetaData
    # ------------------------------------------------------------------

    def _resolve_database_name(self) -> str:
        """Resolve the database name across dialects (no MSSQL-only assumptions)."""
        dialect = self.engine.dialect.name
        query = {
            "mssql": "SELECT DB_NAME()",
            "postgresql": "SELECT current_database()",
            "mysql": "SELECT DATABASE()",
            "mariadb": "SELECT DATABASE()",
        }.get(dialect)
        if query:
            try:
                with self.engine.connect() as conn:
                    value = conn.scalar(text(query))
                if value:
                    return str(value)
            except Exception as exc:
                logger.debug("Database-name query failed for dialect=%s: %s", dialect, exc)
        url_db = self.engine.url.database
        if url_db:
            from pathlib import Path

            return Path(url_db).name or url_db
        return "unknown"

    def _get_schemas(self, target_schemas: list[str]) -> list[dict[str, Any]]:
        return [{"schema_name": s} for s in sorted(target_schemas)]

    def _get_tables(self, metadata: MetaData, schemas: list[str]) -> list[dict[str, Any]]:
        tables = []
        for table in metadata.tables.values():
            if table.schema not in schemas:
                continue
            try:
                comment = self.inspector.get_table_comment(table.name, schema=table.schema)
                description = comment.get("text") if comment else None
            except Exception:
                # Some dialects (e.g. SQLite) don't support table comments.
                description = None
            tables.append(
                {
                    "schema_name": table.schema,
                    "table_name": table.name,
                    "object_id": table.info.get("object_id"),
                    "create_date": table.info.get("create_date"),
                    "modify_date": table.info.get("modify_date"),
                    "type_desc": "USER_TABLE",
                    "table_description": description,
                }
            )
        return tables

    def _get_columns(self, metadata: MetaData, schemas: list[str]) -> list[dict[str, Any]]:
        columns = []
        for table in metadata.tables.values():
            if table.schema not in schemas:
                continue
            for i, col in enumerate(table.columns, start=1):
                col_type = col.type
                identity_info = col.info.get("identity")
                col.info.get("computed")
                default_obj = col.default
                default_value = (
                    default_obj.arg.text if default_obj and hasattr(default_obj, "arg") else None
                )

                # Not all types have 'collation' attribute (e.g., Integer, Float, etc.)
                collation = getattr(col_type, "collation", None)

                columns.append(
                    {
                        "schema_name": table.schema,
                        "table_name": table.name,
                        "object_id": table.info.get("object_id"),
                        "column_name": col.name,
                        "column_id": i,
                        "data_type": str(col_type),
                        "max_length": getattr(col_type, "length", None),
                        "precision": getattr(col_type, "precision", None),
                        "scale": getattr(col_type, "scale", None),
                        "is_nullable": col.nullable,
                        "is_identity": bool(identity_info),
                        "identity_seed_value": identity_info["start"] if identity_info else None,
                        "identity_increment_value": identity_info["increment"]
                        if identity_info
                        else None,
                        "is_computed": col.computed is not None,
                        "computed_definition": col.computed.definition if col.computed else None,
                        "default_value": default_value,
                        "collation_name": collation,
                        "column_description": col.comment,
                    }
                )
        return columns

    def _get_primary_keys(self, metadata: MetaData, schemas: list[str]) -> list[dict[str, Any]]:
        pks = []
        for table in metadata.tables.values():
            if table.schema not in schemas or not table.primary_key:
                continue
            for i, col in enumerate(table.primary_key.columns, start=1):
                pks.append(
                    {
                        "schema_name": table.schema,
                        "table_name": table.name,
                        "constraint_name": table.primary_key.name or f"PK_{table.name}",
                        "object_id": table.info.get("object_id"),
                        "column_name": col.name,
                        "key_ordinal": i,
                        "is_descending_key": False,  # Not available via reflection
                    }
                )
        return pks

    def _get_foreign_keys(self, metadata: MetaData, schemas: list[str]) -> list[dict[str, Any]]:
        fks = []
        for table in metadata.tables.values():
            if table.schema not in schemas:
                continue
            for fk in table.foreign_keys:
                fks.append(
                    {
                        "schema_name": table.schema,
                        "table_name": table.name,
                        "constraint_name": fk.constraint.name,
                        "object_id": table.info.get("object_id"),
                        "column_name": fk.parent.name,
                        "referenced_schema": fk.column.table.schema,
                        "referenced_table": fk.column.table.name,
                        "referenced_column": fk.column.name,
                        "on_delete": fk.constraint.ondelete,
                        "on_update": fk.constraint.onupdate,
                        "is_disabled": False,  # Not available via reflection
                    }
                )
        return fks

    def _get_indexes(self, metadata: MetaData, schemas: list[str]) -> list[dict[str, Any]]:
        indexes = []
        for table in metadata.tables.values():
            if table.schema not in schemas:
                continue
            for idx in table.indexes:
                if idx.unique and (idx.name or "").startswith("PK_"):
                    continue  # Skip PKs
                for i, col in enumerate(idx.columns, start=1):
                    indexes.append(
                        {
                            "schema_name": table.schema,
                            "table_name": table.name,
                            "index_name": idx.name,
                            "object_id": table.info.get("object_id"),
                            "is_unique": idx.unique,
                            "is_primary_key": False,
                            "is_unique_constraint": False,
                            "type_desc": idx.dialect_options.get("mssql", {}).get("type"),
                            "filter_definition": idx.dialect_options.get("mssql", {}).get("where"),
                            "column_name": col.name,
                            "key_ordinal": i,
                            "is_descending_key": False,
                            "is_included_column": False,
                        }
                    )
        return indexes

    def _get_unique_constraints(
        self, metadata: MetaData, schemas: list[str]
    ) -> list[dict[str, Any]]:
        ucs = []
        for table in metadata.tables.values():
            if table.schema not in schemas:
                continue
            for uc in table.constraints:
                if uc.__class__.__name__ == "UniqueConstraint" and not (uc.name or "").startswith(
                    "PK_"
                ):
                    for i, col in enumerate(uc.columns, start=1):
                        ucs.append(
                            {
                                "schema_name": table.schema,
                                "table_name": table.name,
                                "constraint_name": uc.name,
                                "object_id": table.info.get("object_id"),
                                "column_name": col.name,
                                "key_ordinal": i,
                            }
                        )
        return ucs

    def _get_check_constraints(
        self, metadata: MetaData, schemas: list[str]
    ) -> list[dict[str, Any]]:
        ccs = []
        for table in metadata.tables.values():
            if table.schema not in schemas:
                continue
            for cc in table.constraints:
                if cc.__class__.__name__ == "CheckConstraint":
                    ccs.append(
                        {
                            "schema_name": table.schema,
                            "table_name": table.name,
                            "constraint_name": cc.name,
                            "object_id": table.info.get("object_id"),
                            "definition": str(cc.sqltext),
                            "is_disabled": False,
                        }
                    )
        return ccs

    # Views removed: pipeline intentionally only extracts tables. You can add view extraction
    # back in later if you want derived objects collected; the `_get_views` and
    # `_get_view_columns` helpers were dropped to simplify output.

    # ------------------------------------------------------------------
    # Connection string normalization (unchanged)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_connection_string(connection_string: str) -> str:
        if connection_string.startswith("jdbc:sqlserver://"):
            rest = connection_string[len("jdbc:sqlserver://") :]
            host_port, _, params = rest.partition(";")
            host, _, port = host_port.partition(":")
            database = ""
            user = ""
            password = ""
            driver = "ODBC Driver 18 for SQL Server"
            for part in params.split(";"):
                if not part:
                    continue
                key, _, value = part.partition("=")
                key = key.lower()
                if key == "databasename":
                    database = value
                elif key == "user":
                    user = value
                elif key == "password":
                    password = value
                elif key == "driver":
                    driver = value
            server_part = f"{host},{port}" if port else host
            odbc_parts = [
                f"DRIVER={driver}",
                f"SERVER={server_part}",
                f"DATABASE={database}",
                f"UID={user}",
                f"PWD={password}",
                "Encrypt=yes",
                "TrustServerCertificate=yes",
            ]
            from urllib.parse import quote_plus

            return f"mssql+pyodbc:///?odbc_connect={quote_plus(';'.join(odbc_parts))}"
        return connection_string


__all__ = ["SQLServerMetadataExtractor"]
