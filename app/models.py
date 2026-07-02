"""Application data models used across the SQL Insight agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class DatabaseSettings(BaseModel):
    """Configuration options for a single database target."""

    connection_string: str = Field(..., min_length=1)
    # `ddl_file` was removed: schema artifacts are stored under database_schemas/<db_flag>/schema
    intro_template: str = Field(..., description="Path to the business intro template file")
    description: str | None = None
    max_rows: int = Field(1000, ge=1, description="Maximum rows the agent should fetch")
    query_timeout: int = Field(30, ge=1, description="Query timeout in seconds")
    exclude_column_matches: bool = Field(
        False, description="Skip column name/keyword matches when searching tables"
    )
    db_type: str | None = Field(
        None, description="Database dialect identifier e.g., mssql, postgres, mysql"
    )

    # No path coercion needed now that ddl_file is removed


class ApplicationConfig(BaseModel):
    """Top-level configuration for the agent runtime."""

    databases: dict[str, DatabaseSettings]


class TimeRange(BaseModel):
    """Represents an optional time range filter for business intent."""

    start: str | None = Field(
        None, description="ISO date or datetime string for the start of the range"
    )
    end: str | None = Field(
        None, description="ISO date or datetime string for the end of the range"
    )
    grain: str | None = Field(None, description="Optional grain such as day, week, month")


class MetricSpec(BaseModel):
    """Requested metric with optional aggregation and column mapping."""

    name: str = Field(..., description="Business-friendly metric name, e.g. 'total_invoices'")
    aggregation: str = Field(..., description="Aggregation function, e.g. COUNT, SUM, AVG")
    column: str | None = Field(None, description="Concrete column to aggregate once resolved")
    description: str | None = None


class DimensionSpec(BaseModel):
    """Dimension/grouping requested by the user."""

    name: str = Field(..., description="Business-friendly dimension name, e.g. 'client', 'state'")
    column: str | None = Field(None, description="Concrete column once mapped")
    grain: str | None = Field(None, description="Temporal grain if applicable")
    description: str | None = None


class FilterSpec(BaseModel):
    """Structured filter definition produced by the business intent agent."""

    field: str = Field(..., description="Business field being filtered")
    operator: str = Field(..., description="Comparison operator, e.g. '=', 'between', 'in'")
    values: list[str] | None = Field(None, description="Optional list of values for the comparison")
    column: str | None = Field(None, description="Resolved physical column if known")
    free_text: str | None = Field(
        None, description="Fallback natural language description of the filter"
    )


class BusinessQuerySpec(BaseModel):
    """Normalized representation of the user's intent before SQL generation."""

    intent: str = Field(..., description="Brief summary of the analytical question")
    entities: list[str] = Field(default_factory=list, description="Key business entities mentioned")
    metrics: list[MetricSpec] = Field(default_factory=list)
    dimensions: list[DimensionSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_range: TimeRange | None = None
    limit: int | None = Field(None, ge=1, description="Optional row limit requested")
    notes: str | None = Field(None, description="Any clarifying notes produced by the intent agent")


class ColumnInfo(BaseModel):
    """Detailed metadata for a single table column sourced from YAML artifacts."""

    name: str
    data_type: str | None = None
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    is_primary_key: bool = False
    is_foreign_key: bool = False
    is_nullable: bool | None = None
    references: str | None = Field(None, description="Target table if column is a foreign key")


class ForeignKeyInfo(BaseModel):
    """Describes a foreign key relationship for a table."""

    name: str | None = None
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]
    relationship_type: str | None = Field(None, description="many_to_one, one_to_many, etc.")


class TableDetail(BaseModel):
    """Complete table metadata used during planning and SQL generation."""

    table_name: str
    db_schema: str = Field(..., description="Database schema that owns the table")
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    columns: list[ColumnInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)


class TableMatch(BaseModel):
    """Result returned by schema search utilities."""

    table_name: str
    score: float
    reason: str | None = None
    description: str | None = None
    columns: Sequence[str] = Field(default_factory=tuple)


class JoinStep(BaseModel):
    """One hop inside a join path between two tables."""

    from_table: str
    to_table: str
    columns: list[str]
    referenced_columns: list[str]
    relationship_type: str | None = None


class JoinPath(BaseModel):
    """Represents a viable set of joins connecting two tables."""

    source: str
    target: str
    steps: list[JoinStep]
    length: int


class ColumnDocumentation(BaseModel):
    """Documentation for a single database column.

    This model ensures that each column has a business-friendly description
    and searchable keywords for non-technical users.
    """

    column_name: str = Field(description="Name of the database column")
    description: str = Field(
        description="Business-friendly description of the column (1-2 sentences)",
        min_length=10,
        max_length=600,
    )
    keywords: list[str] = Field(
        description="Exactly 3 business-friendly keywords that non-technical users would use",
        min_length=3,
        max_length=3,
    )


class TableDocumentation(BaseModel):
    """Documentation for all columns and the rewritten table description.

    The LLM returns the rewritten narrative for the table using the original
    short description plus the business intro, along with the column
    documentation objects so we can update every YAML file consistently.
    """

    table_description: str = Field(
        description="LLM-generated table description\nusing the provided context", min_length=20
    )
    columns: list[ColumnDocumentation] = Field(
        description="List of column documentation objects",
        min_length=1,
    )


class QueryRequest(BaseModel):
    """Request model for natural language SQL query.

    This model was previously defined in `app.main`. It is moved here to keep
    Pydantic models consolidated inside `app.models` for better reusability
    and readability.
    """

    query: str = Field(..., min_length=1, description="Natural language query")
    db_flag: str = Field(
        ..., min_length=1, description="Target database (e.g., 'medical_db_prod', 'inventory_db')"
    )
    output_format: str = Field(
        default="json",
        description="Output format: json, csv, or table",
        pattern="^(json|csv|table)$",
    )
    user_id: str | None = Field(
        default=None,
        description="User identifier for conversation tracking (optional)",
    )
    session_id: str | None = Field(
        default=None,
        description="Session identifier for conversation context (optional)",
    )
    # Pagination support (page is 1-indexed; page_size is number of rows per page)
    page: int | None = Field(None, ge=1, description="Page number for paged results (1-indexed)")
    page_size: int | None = Field(None, ge=1, description="Number of rows per page")
    include_total: bool | None = Field(
        False, description="Whether to compute total rows using an extra COUNT() query"
    )


class ExecutionMetadata(BaseModel):
    """Metadata about query execution."""

    execution_time_ms: float | None = Field(None, description="Execution time in milliseconds")
    total_rows: int | None = Field(None, description="Total rows returned")

    @field_validator("execution_time_ms", mode="before")
    def round_value(cls, v: any, info: ValidationInfo) -> float:
        if isinstance(v, (float, int)):
            return round(v, 2)
        return v


class QueryResultData(BaseModel):
    """Structured metadata returned for a successful query."""

    results: Any = Field(..., description="Primary result payload in the requested format")
    sql: str = Field(..., description="SQL that generated the payload")
    row_count: int = Field(..., description="Total number of rows returned")
    execution_time_ms: float | None = Field(None, description="Execution time in milliseconds")
    csv: str = Field(..., description="Full result set serialized as CSV")
    raw_json: str = Field(..., description="Full result set serialized as JSON")
    describe: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Describe() summary per column"
    )
    describe_text: str = Field("", description="Textual `describe()` output")
    # Pagination metadata
    page: int | None = Field(None, description="Current page number (1-indexed)")
    page_size: int | None = Field(None, description="Page size used to fetch results")
    has_next: bool | None = Field(
        False, description="Whether there are more rows beyond the returned page"
    )
    total_rows: int | None = Field(
        None,
        description="Total number of rows in the full result set (may require an extra count query)",
    )


class QueryResponse(BaseModel):
    """Response model for query execution."""

    status: str = Field(..., description="Response status (success or error)")
    sql: str | None = Field(None, description="Generated SQL query")
    validation_passed: bool | None = Field(None, description="Whether SQL passed validation")
    data: QueryResultData | None = Field(None, description="Query results envelope")
    error: str | None = Field(None, description="Error message if status is error")
    selected_tables: list[str] | None = Field(None, description="Tables selected for this query")
    follow_up_questions: list[str] | None = Field(
        None,
        description="Additional follow-up questions the agent would ask to clarify intent",
    )
    metadata: ExecutionMetadata = Field(default_factory=ExecutionMetadata)
    natural_summary: str | None = Field(
        None,
        description="LLM-generated natural language summary of the returned dataset",
    )


class DatabaseSummary(BaseModel):
    """Non-sensitive metadata for one enrolled database (never the connection string)."""

    db_flag: str
    db_type: str = ""
    description: str | None = None
    is_public: bool = False


class DatabasesResponse(BaseModel):
    """The enrolled databases the caller may query."""

    databases: list[DatabaseSummary] = Field(default_factory=list)


class SchemaEmbeddingRequest(BaseModel):
    """Request payload for the schema embedding generator."""

    db_flag: str = Field(
        ...,
        min_length=1,
        description="Target database flag (artifacts live under database_schemas/<db_flag>/schema)",
    )
    collection_name: str = Field(
        "crm_db_docs",
        min_length=1,
        description="Postgres PGVector collection name to persist embeddings",
    )


class SchemaEmbeddingResponse(BaseModel):
    """Simple response describing the work completed for a schema flag."""

    db_flag: str
    output_directory: str
    processed_files: list[str] = Field(default_factory=list)
    message: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field("healthy")
    message: str = Field("SQL Insight Agent is running")
    version: str = Field("1.0.0")


class SchemaPipelineRequest(BaseModel):
    """Request payload for the full schema pipeline API."""

    db_flag: str = Field(..., min_length=1, description="Target schema folder flag")
    db_type: str = Field(..., description="Database provider identifier, e.g. sqlserver")
    connection_string: str = Field(
        ..., min_length=1, description="Connection string for the target database"
    )
    description: str | None = Field(None, description="User-friendly description of the database")
    intro_template: str | None = Field(None, description="Path to the business intro template")
    exclude_column_matches: bool = Field(
        False, description="Skip column name/keyword matches when searching tables"
    )
    include_schemas: list[str] | None = Field(None, description="Optional schema whitelist")
    exclude_schemas: list[str] | None = Field(None, description="Optional schema blacklist")
    run_documentation: bool = Field(True, description="Whether to run the documentation stage")
    incremental_documentation: bool = Field(
        True, description="Skip tables that are already fully documented"
    )
    run_embeddings: bool = Field(True, description="Whether to run the embedding stage")


class ExtractionStageSummary(BaseModel):
    status: Literal["success", "failed"]
    output_directory: str
    tables_exported: int
    message: str | None = None


class DocumentationStageSummary(BaseModel):
    status: Literal["success", "failed", "skipped"]
    tables_total: int
    documented: int
    failed: int
    message: str | None = None


class EmbeddingStageSummary(BaseModel):
    status: Literal["success", "failed", "skipped"]
    minimal_files: int
    document_chunks: int
    output_directory: str
    message: str | None = None


class SchemaPipelineResponse(BaseModel):
    db_flag: str
    extraction: ExtractionStageSummary
    documentation: DocumentationStageSummary
    embeddings: EmbeddingStageSummary
    report: SchemaPipelineReport


@dataclass(frozen=True)
class SchemaPipelineReport:
    extracted_files: int
    documentation_tables_total: int
    documentation_documented: int
    documentation_failed: int
    documentation_skipped: int
    embeddings_minimal_files: int
    embeddings_document_chunks: int


@dataclass(frozen=True)
class SchemaDocumentationSummary:
    """Aggregate metrics produced by a schema documentation run."""

    tables_total: int
    documented: int
    failed: int


# ---------------------------------------------------------------------
# Schema pipeline dataclasses (moved from app/schema_pipeline/models.py)
# ---------------------------------------------------------------------


@dataclass(slots=True)
class RawMetadata:
    """Container for the raw metadata rows fetched from SQL Server."""

    database_name: str
    schemas: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    primary_keys: list[dict[str, Any]]
    foreign_keys: list[dict[str, Any]]
    indexes: list[dict[str, Any]]
    unique_constraints: list[dict[str, Any]]
    check_constraints: list[dict[str, Any]]
    # views and view_columns removed: pipeline only extracts tables and related metadata


@dataclass(slots=True)
class DatabaseSchemaArtifacts:
    """Structured payload ready for YAML/index generation."""

    database_name: str
    extracted_at: str
    schemas: dict[str, dict[str, dict[str, Any]]]
    schema_index: dict[str, Any]
    metadata_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SectionContent:
    """Represents a structured section of schema documentation."""

    name: str
    text: str


@dataclass(slots=True)
class StructuredSchemaData:
    """Complete structured representation of a schema table."""

    table_name: str
    schema: str
    minimal_summary: str
    sections: list[SectionContent]


@dataclass(slots=True, frozen=True)
class SchemaEmbeddingSettings:
    schema_root: Path
    minimal_output_root: Path
    chunk_size: int = 2000
    chunk_overlap: int = 100
    embedding_model: str = "jinaai/jina-embeddings-v3"
    model_kwargs: dict | None = None
    collection_name: str = "boxmaster_docs"
    embedding_mode: str = "structured"


@dataclass(slots=True, frozen=True)
class SchemaEmbeddingResult:
    minimal_files: list[Path]
    document_chunks: int


@dataclass(slots=True)
class BuilderSettings:
    include_schemas: list[str] | None = None
    exclude_schemas: list[str] | None = None


@dataclass(slots=True, frozen=True)
class SchemaPipelineResult:
    extraction_output: Path
    tables_exported: int
    documentation_summary: object | None
    embedding_result: object | None
