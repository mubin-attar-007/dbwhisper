"""Orchestrator that runs schema extraction, documentation, and embeddings in one flow."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from time import perf_counter

from app.models import SchemaDocumentationSummary, SchemaEmbeddingResult, SchemaEmbeddingSettings
from app.schema_pipeline.embedding_pipeline import SchemaEmbeddingPipeline
from app.schema_pipeline.pipeline import SchemaExtractionPipeline
from app.schema_pipeline.schema_documenting import document_database_schema
from app.user_db_config_loader import PROJECT_ROOT, get_user_database_settings
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


from app.models import SchemaPipelineResult


class SchemaPipelineOrchestrator:
    """Runs extraction → documentation → embeddings and reports summarised data."""

    def __init__(
        self,
        db_flag: str,
        *,
        include_schemas: Iterable[str] | None = None,
        exclude_schemas: Iterable[str] | None = None,
        embedding_mode: str = "structured",
        run_documentation: bool = True,
        incremental_documentation: bool = True,
        run_embeddings: bool = True,
    ) -> None:

        self.db_flag = db_flag
        self.include_schemas = include_schemas
        self.exclude_schemas = exclude_schemas
        self.collection_name = f"{db_flag}_docs"
        self.chunk_size = 2000
        self.chunk_overlap = 100
        self.embedding_mode = embedding_mode
        self.run_documentation = run_documentation
        self.incremental_documentation = incremental_documentation
        self.run_embeddings = run_embeddings
        self.settings = get_user_database_settings(db_flag)
        self.extraction_output = PROJECT_ROOT / "database_schemas" / db_flag / "schema"
        # Get the Postgres connection string from a central place (not user input)
        # This assumes you have a way to get the project-level Postgres connection string
        # For example, from an environment variable or a config file
        import os

        self.vector_connection_string = os.environ.get("POSTGRES_CONNECTION_STRING")

    def run(self) -> SchemaPipelineResult:
        logger.info("Starting schema pipeline for %s", self.db_flag)
        t0 = perf_counter()
        extraction_path = self._run_extraction()
        t1 = perf_counter()
        extraction_time_s = t1 - t0
        tables_exported = self._count_table_files(extraction_path)
        logger.info(
            "Extraction completed in %.3fs: %s tables exported", extraction_time_s, tables_exported
        )

        documentation_summary = None
        documentation_summary = None
        if self.run_documentation:
            td0 = perf_counter()
            documentation_summary = self._run_documentation(extraction_path)
            td1 = perf_counter()
            logger.info("Documentation stage completed in %.3fs", td1 - td0)

        embedding_result = None
        embedding_result = None
        if self.run_embeddings:
            te0 = perf_counter()
            embedding_result = self._run_embeddings()
            te1 = perf_counter()
            logger.info("Embedding stage completed in %.3fs", te1 - te0)

        total_time = perf_counter() - t0
        logger.info("Total pipeline time for %s: %.3fs", self.db_flag, total_time)

        return SchemaPipelineResult(
            extraction_output=extraction_path,
            tables_exported=tables_exported,
            documentation_summary=documentation_summary,
            embedding_result=embedding_result,
        )

    def _run_extraction(self) -> Path:
        pipeline = SchemaExtractionPipeline(
            self.settings.connection_string,
            self.extraction_output,
            include_schemas=self.include_schemas,
            exclude_schemas=self.exclude_schemas,
            backup_existing=True,
        )
        pipeline.run()
        return self.extraction_output

    def _run_documentation(self, schema_dir: Path) -> SchemaDocumentationSummary:
        intro_path = Path(self.settings.intro_template)
        print(f"Using intro template at: {intro_path} , {intro_path.exists()}, DB_{self.db_flag}")
        summary = document_database_schema(
            database_name=self.db_flag,
            schema_output_dir=schema_dir,
            intro_template_path=intro_path,
            incremental=self.incremental_documentation,
        )
        return summary

    def _run_embeddings(self) -> SchemaEmbeddingResult:
        connection = self.vector_connection_string
        if not connection:
            raise ValueError(
                "POSTGRES_CONNECTION_STRING environment variable is required to embed schemas"
            )

        settings = SchemaEmbeddingSettings(
            schema_root=SchemaEmbeddingPipeline.DEFAULT_SCHEMA_ROOT,
            minimal_output_root=SchemaEmbeddingPipeline.DEFAULT_OUTPUT_ROOT,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            collection_name=self.collection_name,
            embedding_mode=self.embedding_mode,
        )
        pipeline = SchemaEmbeddingPipeline(
            self.db_flag,
            connection,
            settings=settings,
        )
        return pipeline.run()

    def _count_table_files(self, directory: Path) -> int:
        excluded = {"schema_index.yaml", "metadata.yaml"}
        return sum(
            1
            for candidate in directory.rglob("*.yaml")
            if candidate.is_file() and candidate.name not in excluded
        )
