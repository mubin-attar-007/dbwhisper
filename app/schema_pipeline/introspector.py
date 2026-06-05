# """Low-level SQL Server metadata extraction helpers."""

# from __future__ import annotations

# from typing import Any, Dict, Iterable, List, Optional, Sequence

# from sqlalchemy import text
# from sqlalchemy.engine import Engine

# from db.database_manager import get_engine
# from sqlalchemy.exc import SQLAlchemyError

# from app.models import RawMetadata
# from app.utils.logger import setup_logging

# logger = setup_logging(__name__)


# class SQLServerMetadataExtractor:
#     """Extracts comprehensive metadata from SQL Server using system catalogs."""

#     DEFAULT_EXCLUDE_SCHEMAS: Sequence[str] = (
#         "sys",
#         "INFORMATION_SCHEMA",
#         "guest",
#         "db_owner",
#         "db_accessadmin",
#         "db_securityadmin",
#         "db_ddladmin",
#         "db_backupoperator",
#         "db_datareader",
#         "db_datawriter",
#         "db_denydatareader",
#         "db_denydatawriter",
#     )

#     def __init__(
#         self,
#         connection_string: str,
#         include_schemas: Optional[Iterable[str]] = None,
#         exclude_schemas: Optional[Iterable[str]] = None,
#     ) -> None:
#         self.connection_string = self._normalize_connection_string(connection_string)
#         self.include_schemas = {s.lower() for s in include_schemas or []}
#         base_excludes = {s.lower() for s in self.DEFAULT_EXCLUDE_SCHEMAS}
#         extra_excludes = {s.lower() for s in (exclude_schemas or [])}
#         self.exclude_schemas = base_excludes | extra_excludes
#         logger.debug("Initialised SQLServerMetadataExtractor include=%s exclude=%s", self.include_schemas, self.exclude_schemas)
#         self.engine: Engine = get_engine(self.connection_string)

#     def extract(self) -> RawMetadata:
#         """Fetch metadata for all requested schemas and return a structured payload."""

#         with self.engine.begin() as connection:
#             database_name = self._safe_scalar(connection, "SELECT DB_NAME()") or "unknown"
#             logger.info("Extracting schema metadata from database '%s'", database_name)

#             rows = {
#                 "schemas": self._filter_by_schema(self._fetch_rows(connection, self._schemas_sql())),
#                 "tables": self._filter_by_schema(self._fetch_rows(connection, self._tables_sql())),
#                 "columns": self._filter_by_schema(self._fetch_rows(connection, self._columns_sql())),
#                 "primary_keys": self._filter_by_schema(self._fetch_rows(connection, self._primary_keys_sql())),
#                 "foreign_keys": self._filter_by_schema(self._fetch_rows(connection, self._foreign_keys_sql())),
#                 "indexes": self._filter_by_schema(self._fetch_rows(connection, self._indexes_sql())),
#                 "unique_constraints": self._filter_by_schema(self._fetch_rows(connection, self._unique_constraints_sql())),
#                 "check_constraints": self._filter_by_schema(self._fetch_rows(connection, self._check_constraints_sql())),
#                 "views": self._filter_by_schema(self._fetch_rows(connection, self._views_sql())),
#                 "view_columns": self._filter_by_schema(self._fetch_rows(connection, self._view_columns_sql())),
#                 "procedures": self._filter_by_schema(self._fetch_rows(connection, self._procedures_sql())),
#                 "procedure_parameters": self._filter_by_schema(self._fetch_rows(connection, self._procedure_parameters_sql())),
#                 "functions": self._filter_by_schema(self._fetch_rows(connection, self._functions_sql())),
#                 "function_parameters": self._filter_by_schema(self._fetch_rows(connection, self._function_parameters_sql())),
#             }

#         logger.info(
#             "Schema extraction complete: %d tables, %d views, %d procedures, %d functions",
#             len(rows["tables"]),
#             len(rows["views"]),
#             len(rows["procedures"]),
#             len(rows["functions"]),
#         )

#         return RawMetadata(database_name=database_name, **rows)  # type: ignore[arg-type]

#     # ------------------------------------------------------------------
#     # SQL query builders
#     # ------------------------------------------------------------------

#     def _schemas_sql(self) -> str:
#         return (
#             "SELECT schema_id, name AS schema_name "
#             "FROM sys.schemas "
#             "WHERE name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY name;"
#         )

#     def _tables_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, t.name AS table_name, t.object_id, "
#             "       t.create_date, t.modify_date, t.type_desc, "
#             "       CAST(ep.value AS NVARCHAR(MAX)) AS table_description "
#             "FROM sys.tables t "
#             "INNER JOIN sys.schemas s ON t.schema_id = s.schema_id "
#             "LEFT JOIN sys.extended_properties ep ON ep.major_id = t.object_id "
#             "  AND ep.minor_id = 0 AND ep.name = 'MS_Description' "
#             "WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, t.name;"
#         )

#     def _columns_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, t.name AS table_name, t.object_id, c.name AS column_name, "
#             "       c.column_id, ty.name AS data_type, c.max_length, c.precision, c.scale, c.is_nullable, "
#             "       CASE WHEN ic.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_identity, "
#             "       CAST(ic.seed_value AS NVARCHAR(128)) AS identity_seed_value, "
#             "       CAST(ic.increment_value AS NVARCHAR(128)) AS identity_increment_value, c.is_computed, "
#             "       CAST(cc.definition AS NVARCHAR(MAX)) AS computed_definition, "
#             "       CAST(dc.definition AS NVARCHAR(MAX)) AS default_value, c.collation_name, "
#             "       CAST(ep.value AS NVARCHAR(MAX)) AS column_description "
#             "FROM sys.columns c "
#             "INNER JOIN sys.tables t ON c.object_id = t.object_id "
#             "INNER JOIN sys.schemas s ON t.schema_id = s.schema_id "
#             "INNER JOIN sys.types ty ON c.user_type_id = ty.user_type_id "
#             "LEFT JOIN sys.identity_columns ic ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
#             "LEFT JOIN sys.computed_columns cc ON c.object_id = cc.object_id AND c.column_id = cc.column_id "
#             "LEFT JOIN sys.default_constraints dc ON c.default_object_id = dc.object_id "
#             "LEFT JOIN sys.extended_properties ep ON ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description' "
#             "WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, t.name, c.column_id;"
#         )

#     def _primary_keys_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, t.name AS table_name, i.name AS constraint_name, i.object_id, "
#             "       COL_NAME(ic.object_id, ic.column_id) AS column_name, ic.key_ordinal, ic.is_descending_key, i.is_primary_key "
#             "FROM sys.indexes i "
#             "INNER JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id "
#             "INNER JOIN sys.tables t ON i.object_id = t.object_id "
#             "INNER JOIN sys.schemas s ON t.schema_id = s.schema_id "
#             "WHERE i.is_primary_key = 1 "
#             "ORDER BY s.name, t.name, ic.key_ordinal;"
#         )

#     def _foreign_keys_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, t.name AS table_name, fk.name AS constraint_name, fk.object_id, "
#             "       COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS column_name, "
#             "       rs.name AS referenced_schema, rt.name AS referenced_table, "
#             "       COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS referenced_column, "
#             "       fk.delete_referential_action_desc AS on_delete, fk.update_referential_action_desc AS on_update, fk.is_disabled "
#             "FROM sys.foreign_keys fk "
#             "INNER JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id "
#             "INNER JOIN sys.tables t ON fk.parent_object_id = t.object_id "
#             "INNER JOIN sys.schemas s ON t.schema_id = s.schema_id "
#             "INNER JOIN sys.tables rt ON fk.referenced_object_id = rt.object_id "
#             "INNER JOIN sys.schemas rs ON rt.schema_id = rs.schema_id "
#             "ORDER BY s.name, t.name, fk.name, fkc.constraint_column_id;"
#         )

#     def _indexes_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, t.name AS table_name, i.name AS index_name, i.object_id, i.is_unique, i.is_primary_key, i.is_unique_constraint, "
#             "       i.type_desc, i.filter_definition, COL_NAME(ic.object_id, ic.column_id) AS column_name, ic.key_ordinal, ic.is_descending_key, ic.is_included_column "
#             "FROM sys.indexes i "
#             "INNER JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id "
#             "INNER JOIN sys.tables t ON i.object_id = t.object_id "
#             "INNER JOIN sys.schemas s ON t.schema_id = s.schema_id "
#             "WHERE i.is_hypothetical = 0 AND i.is_disabled = 0 AND i.is_primary_key = 0 "
#             "ORDER BY s.name, t.name, i.name, ic.key_ordinal;"
#         )

#     def _unique_constraints_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, t.name AS table_name, kc.name AS constraint_name, kc.object_id, "
#             "       COL_NAME(ic.object_id, ic.column_id) AS column_name, ic.key_ordinal "
#             "FROM sys.key_constraints kc "
#             "INNER JOIN sys.tables t ON kc.parent_object_id = t.object_id "
#             "INNER JOIN sys.schemas s ON t.schema_id = s.schema_id "
#             "INNER JOIN sys.index_columns ic ON kc.parent_object_id = ic.object_id AND kc.unique_index_id = ic.index_id "
#             "WHERE kc.type = 'UQ' "
#             "ORDER BY s.name, t.name, kc.name, ic.key_ordinal;"
#         )

#     def _check_constraints_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, t.name AS table_name, cc.name AS constraint_name, cc.object_id, cc.definition, cc.is_disabled "
#             "FROM sys.check_constraints cc "
#             "INNER JOIN sys.tables t ON cc.parent_object_id = t.object_id "
#             "INNER JOIN sys.schemas s ON t.schema_id = s.schema_id "
#             "ORDER BY s.name, t.name, cc.name;"
#         )

#     def _views_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, v.name AS view_name, v.object_id, v.create_date, v.modify_date, "
#             "       OBJECT_DEFINITION(v.object_id) AS definition, CAST(ep.value AS NVARCHAR(MAX)) AS view_description "
#             "FROM sys.views v "
#             "INNER JOIN sys.schemas s ON v.schema_id = s.schema_id "
#             "LEFT JOIN sys.extended_properties ep ON ep.major_id = v.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description' "
#             "WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, v.name;"
#         )

#     def _view_columns_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, v.name AS view_name, c.name AS column_name, c.column_id, ty.name AS data_type, c.max_length, c.is_nullable, "
#             "       CAST(ep.value AS NVARCHAR(MAX)) AS column_description "
#             "FROM sys.columns c "
#             "INNER JOIN sys.views v ON c.object_id = v.object_id "
#             "INNER JOIN sys.schemas s ON v.schema_id = s.schema_id "
#             "INNER JOIN sys.types ty ON c.user_type_id = ty.user_type_id "
#             "LEFT JOIN sys.extended_properties ep ON ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description' "
#             "WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, v.name, c.column_id;"
#         )

#     def _procedures_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, p.name AS procedure_name, p.object_id, p.create_date, p.modify_date, "
#             "       OBJECT_DEFINITION(p.object_id) AS definition, CAST(ep.value AS NVARCHAR(MAX)) AS procedure_description "
#             "FROM sys.procedures p "
#             "INNER JOIN sys.schemas s ON p.schema_id = s.schema_id "
#             "LEFT JOIN sys.extended_properties ep ON ep.major_id = p.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description' "
#             "WHERE p.is_ms_shipped = 0 AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, p.name;"
#         )

#     def _procedure_parameters_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, p.name AS procedure_name, pm.name AS parameter_name, ty.name AS data_type, pm.max_length, pm.is_output, pm.has_default_value, pm.default_value "
#             "FROM sys.parameters pm "
#             "INNER JOIN sys.procedures p ON pm.object_id = p.object_id "
#             "INNER JOIN sys.schemas s ON p.schema_id = s.schema_id "
#             "INNER JOIN sys.types ty ON pm.user_type_id = ty.user_type_id "
#             "WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, p.name, pm.parameter_id;"
#         )

#     def _functions_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, o.name AS function_name, o.object_id, o.type_desc AS function_type, o.create_date, o.modify_date, "
#             "       OBJECT_DEFINITION(o.object_id) AS definition, CAST(ep.value AS NVARCHAR(MAX)) AS function_description "
#             "FROM sys.objects o "
#             "INNER JOIN sys.schemas s ON o.schema_id = s.schema_id "
#             "LEFT JOIN sys.extended_properties ep ON ep.major_id = o.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description' "
#             "WHERE o.type IN ('FN', 'IF', 'TF') AND o.is_ms_shipped = 0 AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, o.name;"
#         )

#     def _function_parameters_sql(self) -> str:
#         return (
#             "SELECT s.name AS schema_name, o.name AS function_name, pm.name AS parameter_name, ty.name AS data_type, pm.max_length, pm.has_default_value, pm.default_value "
#             "FROM sys.parameters pm "
#             "INNER JOIN sys.objects o ON pm.object_id = o.object_id "
#             "INNER JOIN sys.schemas s ON o.schema_id = s.schema_id "
#             "INNER JOIN sys.types ty ON pm.user_type_id = ty.user_type_id "
#             "WHERE o.type IN ('FN', 'IF', 'TF') AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA') "
#             "ORDER BY s.name, o.name, pm.parameter_id;"
#         )

#     # Row count SQL and related logic removed (not needed for read-only users)

#     # ------------------------------------------------------------------
#     # Helpers
#     # ------------------------------------------------------------------

#     def _fetch_rows(self, connection, sql: str) -> List[Dict[str, Any]]:
#         result = connection.execute(text(sql))
#         return [dict(row._mapping) for row in result]

#     # _safe_fetch_rows removed (only used for row count logic)

#     def _filter_by_schema(self, rows: List[Dict[str, Any]], key: str = "schema_name") -> List[Dict[str, Any]]:
#         if not rows:
#             return []
#         filtered: List[Dict[str, Any]] = []
#         for row in rows:
#             schema_name = str(row.get(key) or "").lower()
#             if schema_name and schema_name in self.exclude_schemas:
#                 continue
#             if self.include_schemas and schema_name not in self.include_schemas:
#                 continue
#             filtered.append(row)
#         return filtered

#     @staticmethod
#     def _safe_scalar(connection, sql: str) -> Optional[str]:
#         try:
#             result = connection.execute(text(sql))
#             return result.scalar()
#         except SQLAlchemyError:
#             return None

#     @staticmethod
#     def _normalize_connection_string(connection_string: str) -> str:
#         if connection_string.startswith("jdbc:sqlserver://"):
#             rest = connection_string[len("jdbc:sqlserver://") :]
#             host_port, _, params = rest.partition(";")
#             host, _, port = host_port.partition(":")
#             database = ""
#             user = ""
#             password = ""
#             driver = "ODBC Driver 18 for SQL Server"
#             for part in params.split(";"):
#                 if not part:
#                     continue
#                 key, _, value = part.partition("=")
#                 key = key.lower()
#                 if key == "databasename":
#                     database = value
#                 elif key == "user":
#                     user = value
#                 elif key == "password":
#                     password = value
#                 elif key == "driver":
#                     driver = value
#             server_part = f"{host},{port}" if port else host
#             odbc_parts = [
#                 f"DRIVER={driver}",
#                 f"SERVER={server_part}",
#                 f"DATABASE={database}",
#                 f"UID={user}",
#                 f"PWD={password}",
#                 "Encrypt=yes",
#                 "TrustServerCertificate=yes",
#             ]
#             from urllib.parse import quote_plus

#             return f"mssql+pyodbc:///?odbc_connect={quote_plus(';'.join(odbc_parts))}"
#         return connection_string


# __all__ = ["SQLServerMetadataExtractor"]


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
        """Extract all metadata using SQLAlchemy reflection."""
        with self.engine.connect() as conn:
            database_name = conn.scalar(text("SELECT DB_NAME()")) or "unknown"

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

    def _get_schemas(self, target_schemas: list[str]) -> list[dict[str, Any]]:
        return [{"schema_name": s} for s in sorted(target_schemas)]

    def _get_tables(self, metadata: MetaData, schemas: list[str]) -> list[dict[str, Any]]:
        tables = []
        for table in metadata.tables.values():
            if table.schema not in schemas:
                continue
            comment = self.inspector.get_table_comment(table.name, schema=table.schema)
            description = comment.get("text") if comment else None
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
                if idx.unique and idx.name.startswith("PK_"):
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
                if uc.__class__.__name__ == "UniqueConstraint" and not uc.name.startswith("PK_"):
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
