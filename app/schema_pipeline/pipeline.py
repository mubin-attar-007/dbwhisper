"""High-level orchestration for the schema extraction pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.models import BuilderSettings
from app.schema_pipeline.builder import SchemaGraphBuilder
from app.schema_pipeline.introspector import SQLServerMetadataExtractor
from app.schema_pipeline.writer import YamlSchemaWriter
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


class SchemaExtractionPipeline:
    """Run the full schema extraction → build → YAML generation flow."""

    def __init__(
        self,
        connection_string: str,
        output_dir: Path,
        *,
        include_schemas: Iterable[str] | None = None,
        exclude_schemas: Iterable[str] | None = None,
        backup_existing: bool = True,
    ) -> None:
        self.connection_string = connection_string
        self.output_dir = output_dir
        self.include_schemas = include_schemas
        self.exclude_schemas = exclude_schemas
        self.backup_existing = backup_existing

    def run(self) -> Path:
        logger.info("Starting schema extraction pipeline. Output => %s", self.output_dir)
        # TODO: Introduce db_type-aware extraction so we can route to the right extractor implementation.
        # TODO: Add an `include_views` flag / BuilderSettings to optionally include view
        # metadata if you want derived objects; for now we strip views for simplicity.
        extractor = SQLServerMetadataExtractor(
            self.connection_string,
            include_schemas=self.include_schemas,
            exclude_schemas=self.exclude_schemas,
        )
        raw_metadata = extractor.extract()
        logger.debug("Extracted raw metadata for database: %s", raw_metadata.database_name)
        builder = SchemaGraphBuilder(
            BuilderSettings(
                include_schemas=self.include_schemas, exclude_schemas=self.exclude_schemas
            )
        )
        artifacts = builder.build(raw_metadata)
        logger.debug("Built database schema artifacts for: %s", artifacts.database_name)
        writer = YamlSchemaWriter(self.output_dir, backup_existing=self.backup_existing)
        writer.write(artifacts)
        logger.info("Wrote schema artifacts to %s", self.output_dir)
        logger.info("Schema extraction pipeline finished successfully.")
        return self.output_dir


__all__ = ["SchemaExtractionPipeline"]
