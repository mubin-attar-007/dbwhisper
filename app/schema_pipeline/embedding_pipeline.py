"""Pipeline for converting schema YAML files to minimal text and generating embeddings."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.retriever import _ensure_pgvector_index
from app.schema_pipeline.minimal_text import yaml_to_minimal_text
from app.schema_pipeline.structured_docs import yaml_to_structured_sections
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


from app.models import SchemaEmbeddingResult, SchemaEmbeddingSettings


class SchemaEmbeddingPipeline:
    """Convert schema YAML definitions into embeddings stored in Postgres."""

    DEFAULT_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "database_schemas"
    DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "temp_output" / "minimal"

    def __init__(
        self,
        db_flag: str,
        connection_string: str,
        *,
        settings: SchemaEmbeddingSettings | None = None,
    ) -> None:
        self.db_flag = db_flag
        if not connection_string:
            raise ValueError("POSTGRES_CONNECTION_STRING is required to persist embeddings")
        self.connection_string = connection_string
        self.settings = settings or SchemaEmbeddingSettings(
            schema_root=self.DEFAULT_SCHEMA_ROOT,
            minimal_output_root=self.DEFAULT_OUTPUT_ROOT,
        )
        self.target_dir = self.settings.schema_root / self.db_flag / "schema"
        if not self.target_dir.exists():
            raise FileNotFoundError(f"Schema directory not found: {self.target_dir}")
        from app.core.embeddings import build_embedding_client

        logger.debug(
            "Embedding pipeline using configured provider (settings model=%s)",
            self.settings.embedding_model,
        )
        self._embedding_client = build_embedding_client()

    def run(self) -> SchemaEmbeddingResult:
        """Run conversion for every YAML file and persist embeddings.

        Uses embedding_mode from settings to determine chunking strategy:
        - "minimal": Creates chunks from minimal text summaries
        - "structured": Creates section-aware chunks with rich metadata
        """

        yaml_paths = self._list_yaml_files()
        if not yaml_paths:
            logger.warning("No YAML files found for db_flag=%s", self.db_flag)
            return SchemaEmbeddingResult(minimal_files=[], document_chunks=0)

        mode = self.settings.embedding_mode
        logger.info("Starting embeddings with mode=%s for %d YAML files", mode, len(yaml_paths))

        if mode == "structured":
            documents = self._build_structured_documents(yaml_paths)
            processed_paths = list(yaml_paths)
        else:
            minimal_paths = self._convert_to_minimal(yaml_paths)
            if not minimal_paths:
                logger.warning("No minimal files created for db_flag=%s", self.db_flag)
                return SchemaEmbeddingResult(minimal_files=[], document_chunks=0)
            documents = self._build_documents(minimal_paths)
            processed_paths = [path for path, _, _ in minimal_paths]

        chunk_count = len(documents)
        if documents:
            self._persist_embeddings(documents)
        else:
            logger.warning("No document chunks generated for db_flag=%s", self.db_flag)

        return SchemaEmbeddingResult(minimal_files=processed_paths, document_chunks=chunk_count)

    def _list_yaml_files(self) -> list[Path]:
        excluded = {"schema_index.yaml", "metadata.yaml"}
        exclude_dirs = set()
        files = []
        for child in self.target_dir.rglob("*.yaml"):
            if (
                child.is_file()
                and child.name not in excluded
                and not any(part in exclude_dirs for part in child.parts)
            ):
                files.append(child)
        return sorted(files, key=lambda child: child.name)

    def _extract_table_metadata(self, schema_file: Path) -> tuple[str, str]:
        try:
            with schema_file.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except Exception:
            return schema_file.stem, "dbo"
        return data.get("table_name", schema_file.stem), data.get("schema", "dbo")

    def _convert_to_minimal(self, files: Iterable[Path]) -> list[tuple[Path, str, str]]:
        output_dir = self.settings.minimal_output_root / self.db_flag
        output_dir.mkdir(parents=True, exist_ok=True)
        minimal_paths: list[tuple[Path, str, str]] = []

        for schema_file in files:
            try:
                minimal_text = yaml_to_minimal_text(schema_file)
            except Exception as error:
                logger.warning("Skipping %s: %s", schema_file, error)
                continue

            minimal_file = output_dir / f"{schema_file.stem}_minimal.txt"
            minimal_file.write_text(minimal_text, encoding="utf-8")
            table_name, schema_name = self._extract_table_metadata(schema_file)
            minimal_paths.append((minimal_file, table_name, schema_name))
            logger.info("Wrote minimal schema text: %s", minimal_file)

        return minimal_paths

    def _build_documents(self, minimal_paths: Sequence[tuple[Path, str, str]]) -> list[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
        )
        documents: list[Document] = []
        for minimal_file, table_name, schema_name in minimal_paths:
            text = minimal_file.read_text(encoding="utf-8")
            if not text.strip():
                continue
            for chunk_index, chunk in enumerate(splitter.split_text(text), start=1):
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "table_name": table_name,
                            "schema": schema_name,
                            "db_flag": self.db_flag,
                            "section": "summary",
                            "chunk_type": "table_summary",
                            "chunk_index": chunk_index,
                        },
                    )
                )
        return documents

    def _build_structured_documents(self, yaml_paths: Sequence[Path]) -> list[Document]:
        """Build documents using structured sections with rich metadata.

        This method creates chunks from structured sections (header, columns, keys, etc.)
        with enhanced metadata including section name, chunk type, and chunk index.

        Args:
            yaml_paths: List of YAML file paths to process

        Returns:
            List of Document objects with structured metadata
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
        )
        documents: list[Document] = []

        for yaml_file in yaml_paths:
            try:
                schema_payload = yaml_to_structured_sections(yaml_file)
            except Exception as error:
                from app.utils.logger import sanitize_for_log

                logger.warning(
                    "Failed to build structured documents for %s: %s",
                    yaml_file,
                    sanitize_for_log(str(error), max_len=500),
                )
                continue

            minimal_summary = (schema_payload.get("minimal_summary") or "").strip()
            sections = schema_payload.get("sections", [])
            logger.debug(
                f"Successfully parsed structured sections for {yaml_file} with {len(sections)} sections and minimal summary length {len(minimal_summary)}"
            )
            base_metadata = {
                "schema": schema_payload.get("schema", "dbo"),
                "table_name": schema_payload.get("table_name", yaml_file.stem),
                "db_flag": self.db_flag,
            }
            table_chunk_count = 0
            logger.debug(
                "Building documents for table %s.%s with %d sections",
                base_metadata["schema"],
                base_metadata["table_name"],
                len(sections),
            )
            # Add minimal summary as separate chunk with type "table_summary"
            if minimal_summary:
                documents.append(
                    Document(
                        page_content=minimal_summary,
                        metadata={
                            **base_metadata,
                            "section": "summary",
                            "chunk_type": "table_summary",
                            "chunk_index": 0,
                        },
                    )
                )
                table_chunk_count += 1
            # Process each section into chunks
            for section in sections:
                section_name = section.get("name", "")
                section_text = (section.get("text") or "").strip()

                if not section_text:
                    continue

                # Prepend section header for context
                chunk_source = f"TABLE: {base_metadata['table_name']} SECTION: {section_name.upper()}\n{section_text}"

                for chunk_index, chunk in enumerate(splitter.split_text(chunk_source), start=1):
                    chunk_length = len(chunk)

                    documents.append(
                        Document(
                            page_content=chunk,
                            metadata={
                                **base_metadata,
                                "section": section_name,
                                "chunk_type": "section",
                                "chunk_index": chunk_index,
                            },
                        )
                    )
                    table_chunk_count += 1

                    logger.debug(
                        "[embedding] table=%s section=%s chunk_index=%d chunk_length=%d chunk_size=%d",
                        base_metadata["table_name"],
                        section_name,
                        chunk_index,
                        chunk_length,
                        self.settings.chunk_size,
                    )

            logger.info(
                "Built %d documents for table %s.%s",
                table_chunk_count,
                base_metadata["schema"],
                base_metadata["table_name"],
            )

        return documents

    def _persist_embeddings(self, documents: Sequence[Document]) -> None:
        # Ensure PGVector index and extension before writing
        try:
            _ensure_pgvector_index(self.connection_string)
        except Exception as exc:
            logger.warning("PGVector index creation failed: %s", exc)

        vector_store = PGVector(
            embeddings=self._embedding_client,
            collection_name=self.settings.collection_name,
            connection=self.connection_string,
            use_jsonb=True,
        )
        vector_store.add_documents(list(documents))
        logger.info("Stored %d document chunks for %s", len(documents), self.db_flag)
