"""Agent that generates business-friendly column descriptions and keywords using Groq LLM.

This module uses LangChain's structured output capabilities to generate documentation
for database schemas using LLMs with guaranteed schema validation.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from app.agent.chain import get_llm
from app.models import ColumnDocumentation, TableDocumentation

load_dotenv()

from app.utils.logger import setup_logging

logger = setup_logging(__name__)

from app.models import SchemaDocumentationSummary


class SchemaDocumentingAgent:
    """Generate column descriptions and keywords using LLM with business context."""

    def __init__(
        self,
        provider: str | None = None,
    ) -> None:
        """Initialize the schema documenting agent.

        Args:
            provider: LLM provider to use (e.g. "groq", "openai"). If None, auto-selects.
        """

        self.llm = get_llm(provider)
        self.prompt: ChatPromptTemplate = self._build_prompt()
        # Use with_structured_output for guaranteed schema compliance
        # This is the modern LangChain approach for structured generation
        self.chain: Runnable = self.prompt | self.llm.with_structured_output(
            TableDocumentation, strict=False
        )

    def _build_prompt(self) -> ChatPromptTemplate:
        """Build the prompt template for column documentation.

        Returns:
            ChatPromptTemplate configured for generating column documentation.
        """
        return ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """You are a documentation specialist who rewrites table narratives and column
summaries for business audiences. You have access to the full db intro (``business_intro``) and
should use it to expand the short table description provided by the schema index.

Guidelines:
1. **Table Description**: Rewrite or expand the provided table short description into
   ~2-3 sentences that mention the table's role within the database and reflect the business
   context described in {business_intro}. Keep the tone consistent with the rest of the schema docs.
2. **Column Descriptions**: Produce exactly one short sentence (ideally 1-2 clauses) per column
   explaining what the column means for a business user. Keep them concise and avoid technical-only
   jargon.
3. **Keywords**: Provide exactly 3 business-friendly search terms for each column.
""",
                ),
                (
                    "human",
                    """Table: {table_name}
Schema: {schema_name}
Table Description: {table_description}

Columns to document:
{columns_json}

Rewrite the table description and return the updated narrative plus the column docs as structured output.""",
                ),
            ]
        )

    def document_table(
        self,
        table_name: str,
        schema_name: str,
        table_description: str,
        columns: list[dict[str, Any]],
        business_intro: str,
        max_retries: int = 5,
        initial_delay: float = 10.0,
    ) -> tuple[dict[str, ColumnDocumentation], str]:
        """Generate documentation for all columns in a table using LLM.

        This method uses LangChain's structured output to ensure the LLM response
        conforms to our Pydantic schema for validated documentation.

        Args:
            table_name: Name of the table to document
            schema_name: Schema/database the table belongs to
            table_description: Human-readable description of the table's purpose
            columns: List of column dictionaries with keys: 'name', 'type', etc.
            business_intro: Business context text to guide documentation style

        Returns:
            Tuple containing the column documentation map and the rewritten table description.
            If generation fails, the map will be empty and the table description will fall back
            to the original value.

        Example:
            >>> agent = SchemaDocumentingAgent()
            >>> columns = [{"name": "customer_id", "type": "int"}]
            >>> docs = agent.document_table(
            ...     "orders", "sales", "Customer orders", columns, "E-commerce platform"
            ... )
        """
        logger.info(
            "Starting documentation for table %s.%s with %d columns",
            schema_name,
            table_name,
            len(columns),
        )

        if not columns:
            logger.warning("No columns provided for table %s.%s", schema_name, table_name)
            return {}, table_description

        # Prepare column summary for LLM with relevant metadata
        columns_json = self._build_columns_payload(columns)

        # Trim very large prompt components to stay well within practical limits.
        trimmed_intro, intro_trimmed = self._truncate_text(
            business_intro or "",
            max_chars=6000,
            label="business_intro",
            table_identifier=f"{schema_name}.{table_name}",
        )
        base_description = table_description or "No description available"
        trimmed_description, desc_trimmed = self._truncate_text(
            base_description,
            max_chars=2500,
            label="table_description",
            table_identifier=f"{schema_name}.{table_name}",
        )

        # Build prompt variables early so token-counting and other diagnostics can inspect them
        prompt_vars = {
            "business_intro": trimmed_intro,
            "table_name": table_name,
            "schema_name": schema_name,
            "table_description": trimmed_description,
            "columns_json": columns_json,
        }

        logger.debug(
            "LLM prompt snapshot for %s.%s -> intro_chars=%d (trimmed=%s), table_desc_chars=%d (trimmed=%s), columns=%d",
            schema_name,
            table_name,
            len(trimmed_intro),
            intro_trimmed,
            len(trimmed_description),
            desc_trimmed,
            len(columns),
        )
        from app.utils.logger import sanitize_for_log

        logger.debug("Column payload preview: %s", sanitize_for_log(columns_json, max_len=400))

        # Try to detect the model and a reasonable context limit for warnings
        model_name = (
            getattr(self.llm, "model_name", None)
            or getattr(self.llm, "model", None)
            or str(self.llm)
        )
        model_name = str(model_name)
        attempt = 0
        delay = initial_delay
        # Compute token counts and compare against detected/estimated context window
        try:
            import tiktoken

            enc = None
            try:
                enc = tiktoken.encoding_for_model(model_name)
            except Exception:
                # Fallback to cl100k_base if specific model encoding unknown
                enc = tiktoken.get_encoding("cl100k_base")

            lengths = {k: len(enc.encode(str(v))) for k, v in prompt_vars.items()}
            token_count = sum(lengths.values())
            logger.debug("Prompt token lengths: %s", lengths)
            logger.debug("Total prompt token count: %d", token_count)

            # Try to detect a context limit value on the LLM object (if exposed by provider)
            context_limit = None
            for attr in (
                "context_window",
                "context_length",
                "max_context",
                "max_tokens",
                "context_size",
            ):
                value = getattr(self.llm, attr, None)
                if value:
                    context_limit = value
                    break

            if context_limit:
                try:
                    cl = int(context_limit)
                    logger.debug("Model '%s' reported context limit: %d tokens", model_name, cl)
                    if token_count > cl:
                        logger.warning(
                            "Prompt token count (%d) exceeds reported context limit (%d) for model '%s' — this may cause truncation or function-call failures",
                            token_count,
                            cl,
                            model_name,
                        )
                except Exception:
                    logger.debug(
                        "Non-integer context_limit=%s for model %s", context_limit, model_name
                    )
            else:
                logger.debug(
                    "Model '%s' did not report a context window; skipping limit comparison",
                    model_name,
                )
        except Exception as e:
            logger.debug("Token counting / context detection failed: %s", e)

        prompt_state = dict(prompt_vars)
        fallback_applied = False

        while attempt <= max_retries:
            try:
                # logger.debug(f"Invoking LLM for table {schema_name}.{table_name} documentation with table description: {table_description}")
                # Invoke the chain - structured output ensures type safety
                result: TableDocumentation = self.chain.invoke(prompt_state)

                # Convert list to dict for efficient lookup
                doc_map = {doc.column_name: doc for doc in result.columns}

                logger.info(
                    "Successfully documented %d/%d columns for %s.%s",
                    len(doc_map),
                    len(columns),
                    schema_name,
                    table_name,
                )

                # Warn if we didn't get documentation for all columns
                if len(doc_map) != len(columns):
                    missing = {col["name"] for col in columns} - set(doc_map.keys())
                    logger.warning(
                        "Missing documentation for columns in %s.%s: %s",
                        schema_name,
                        table_name,
                        missing,
                    )

                return doc_map, result.table_description.strip()

            except Exception as exc:
                attempt += 1
                # Extra debug: capture function/tool calling errors and tool_use_failed patterns
                exc_str = str(exc).lower()
                extra_info = {}
                # Attempt to extract structured details from common exception shapes
                for attr in (
                    "status_code",
                    "code",
                    "errors",
                    "response",
                    "raw_response",
                    "body",
                    "args",
                ):
                    try:
                        val = getattr(exc, attr, None)
                        if val is not None:
                            extra_info[attr] = val
                    except Exception:
                        # ignore attribute access errors
                        pass

                if extra_info:
                    from app.utils.logger import sanitize_for_log

                    logger.debug(
                        "Exception extra metadata: %s", sanitize_for_log(extra_info, max_len=800)
                    )

                # If the provider returned a 'failed_generation' body from Groq/OpenAI-style responses,
                # surface it so we can inspect the attempted function/tool call that failed.
                try:
                    body = extra_info.get("body") or extra_info.get("response")
                    # 'body' may be an HTTP response object with .json() available
                    if hasattr(body, "json"):
                        parsed = body.json()
                        fg = (
                            parsed.get("error", {}).get("failed_generation")
                            if isinstance(parsed, dict)
                            else None
                        )
                        if fg:
                            from app.utils.logger import sanitize_for_log

                            logger.debug(
                                "failed_generation payload from API: %s",
                                sanitize_for_log(fg, max_len=800),
                            )
                    elif isinstance(body, dict):
                        fg = body.get("error", {}).get("failed_generation")
                        if fg:
                            from app.utils.logger import sanitize_for_log

                            logger.debug(
                                "failed_generation payload from API: %s",
                                sanitize_for_log(fg, max_len=800),
                            )
                except Exception:
                    # Best-effort; do not fail the whole flow when reading response body
                    logger.debug(
                        "Could not parse failed_generation payload from exception metadata"
                    )

                if (
                    "tool_use_failed" in exc_str
                    or "function" in exc_str
                    or "function calling" in exc_str
                ):
                    logger.error(
                        "Function/tool calling error during documentation of %s.%s: %s",
                        schema_name,
                        table_name,
                        exc,
                        exc_info=True,
                    )
                else:
                    logger.error(
                        "Failed to document table %s.%s: %s",
                        schema_name,
                        table_name,
                        exc,
                        exc_info=True,
                    )
                # Check for rate limit error (429)
                if (
                    hasattr(exc, "status_code") and getattr(exc, "status_code", None) == 429
                ) or "rate limit" in str(exc).lower():
                    logger.warning(
                        f"Rate limit hit (429) for {table_name}. Sleeping {delay:.1f}s before retry {attempt}/{max_retries}..."
                    )
                    time.sleep(delay)
                    delay *= 2  # Exponential backoff
                    continue

                is_tool_failure = (
                    "tool_use_failed" in exc_str
                    or "function" in exc_str
                    or "function calling" in exc_str
                )

                if is_tool_failure and attempt <= max_retries:
                    if not fallback_applied:
                        fallback_applied = True
                        prompt_state = self._apply_compact_prompt(prompt_vars, columns)
                        logger.warning(
                            "Tool call failed for %s.%s — retrying with compact prompt (attempt %d/%d)",
                            schema_name,
                            table_name,
                            attempt,
                            max_retries,
                        )
                        time.sleep(2)
                        continue
                    else:
                        logger.warning(
                            "Tool call failed again for %s.%s even after compact prompt.",
                            schema_name,
                            table_name,
                        )

                # Return empty dict as fallback to allow pipeline to continue
                return {}, table_description
        logger.error(f"Max retries exceeded for {table_name}.{schema_name} due to rate limits.")
        return {}, table_description

    def document_schema(
        self,
        schema_yaml_dir: Path,
        business_intro_path: Path,
        incremental: bool = True,
    ) -> SchemaDocumentationSummary:
        """Process all table YAML files and add column documentation.

        This method reads existing schema YAML files, generates documentation for each
        table's columns using the LLM, and writes the updated YAML files back to disk.

        Args:
            schema_yaml_dir: Directory containing schema YAML files (e.g. /crm_db)
            business_intro_path: Path (or relative path) to business intro text file for context
            incremental: If True, skip tables that are already fully documented.

        Raises:
            FileNotFoundError: If schema_yaml_dir doesn't exist
        """
        if not schema_yaml_dir.exists():
            raise FileNotFoundError(f"Schema directory not found: {schema_yaml_dir}")

        # Resolve business intro path robustly: accept Path or str and try several fallbacks
        resolved_intro_path = None
        try:
            candidate = (
                Path(business_intro_path)
                if not isinstance(business_intro_path, Path)
                else business_intro_path
            )
        except Exception:
            candidate = None

        candidates = []
        if candidate:
            candidates.append(candidate)
        # try relative to schema dir
        if candidate and not candidate.is_absolute():
            candidates.append(schema_yaml_dir.joinpath(candidate))
        # try relative to cwd
        if candidate and not candidate.is_absolute():
            candidates.append(Path.cwd().joinpath(candidate))
        # allow direct string fallback if previously passed a string
        if isinstance(business_intro_path, str):
            candidates.append(Path(business_intro_path))

        for c in candidates:
            try:
                if c and c.exists() and c.is_file():
                    resolved_intro_path = c
                    break
            except Exception:
                continue

        # Load business context
        print(f"Resolved intro path: {resolved_intro_path}, {business_intro_path}")
        if not resolved_intro_path:
            logger.warning(
                "Business intro file not found. Tried locations: %s. Using default context.",
                ", ".join(str(p) for p in candidates if p),
            )
            business_intro = "No business context provided."
        else:
            try:
                business_intro = resolved_intro_path.read_text(encoding="utf-8").strip()
            except Exception as exc:
                from app.utils.logger import sanitize_for_log

                logger.warning(
                    "Failed to read business intro file %s: %s",
                    resolved_intro_path,
                    sanitize_for_log(str(exc), max_len=300),
                )
                business_intro = "No business context provided."

        if not business_intro:
            business_intro = "No business context provided."
            logger.warning("Empty business intro file, using default context")

        logger.info(
            "Starting schema documentation with business intro from: %s",
            resolved_intro_path or business_intro_path,
        )

        schema_index_path = schema_yaml_dir / "schema_index.yaml"
        schema_index_data = self._load_schema_index_data(schema_index_path)
        schema_index_map = self._build_index_map(schema_index_data)

        intro_snippet = self._intro_snippet(business_intro)

        # Find all table YAML files (exclude metadata files)
        yaml_files = list(schema_yaml_dir.rglob("*.yaml"))
        yaml_files = [f for f in yaml_files if f.stem not in ("schema_index", "metadata")]

        if not yaml_files:
            logger.warning("No YAML files found in %s", schema_yaml_dir)
            return SchemaDocumentationSummary(tables_total=0, documented=0, failed=0)

        logger.info("Found %s table YAML files to document", len(yaml_files))

        # Track progress
        successful = 0
        failed = 0

        for idx, yaml_file in enumerate(yaml_files, 1):
            # Use %s for all placeholders so sanitizer can safely stringify inputs.
            logger.info("Processing file %s/%s: %s", idx, len(yaml_files), yaml_file.name)

            try:
                # Load existing table data
                with yaml_file.open("r", encoding="utf-8") as handle:
                    table_data = yaml.safe_load(handle)

                # Validate table data structure
                if not table_data or "columns" not in table_data:
                    logger.warning("Skipping invalid table file: %s", yaml_file)
                    failed += 1
                    continue

                if incremental and self._is_table_fully_documented(table_data):
                    logger.info("Skipping already documented table: %s", yaml_file.name)
                    successful += 1
                    continue

                table_name = table_data.get("table_name", yaml_file.stem)
                schema_name = table_data.get("schema", "dbo")
                table_description = self._table_description_from_index(
                    schema_index_map,
                    schema_name,
                    table_name,
                    table_data.get("description", ""),
                )
                table_description = self._combine_with_intro(
                    table_description,
                    intro_snippet,
                )
                columns = table_data.get("columns", [])

                table_data["description"] = table_description or table_data.get("description", "")

                if not columns:
                    logger.info("No columns to document in %s", yaml_file)
                    successful += 1
                    continue

                # Generate documentation using LLM
                doc_map, rewritten_description = self.document_table(
                    table_name=table_name,
                    schema_name=schema_name,
                    table_description=table_description,
                    columns=columns,
                    business_intro=business_intro,
                )

                table_data["description"] = rewritten_description or table_data.get(
                    "description", ""
                )

                if not doc_map:
                    logger.warning("No documentation generated for %s", yaml_file)
                    failed += 1
                    continue

                # Update column descriptions and keywords
                for col in columns:
                    col_name = col["name"]
                    if col_name in doc_map:
                        col["description"] = doc_map[col_name].description
                        col["keywords"] = doc_map[col_name].keywords
                    else:
                        # Ensure keywords field exists even if not documented
                        col.setdefault("keywords", [])
                        logger.debug("No documentation for column %s in %s", col_name, yaml_file)

                # Update table-level keywords if empty
                if not table_data.get("keywords"):
                    # Aggregate unique keywords from all columns
                    all_keywords = set()
                    for col in columns:
                        all_keywords.update(col.get("keywords", []))
                    # Take top 10 most common keywords (sorted for determinism)
                    table_data["keywords"] = sorted(all_keywords)[:10]
                from app.utils.logger import sanitize_for_log

                logger.debug(
                    "Updated table keywords: %s",
                    sanitize_for_log(table_data["keywords"], max_len=200),
                )

                # Write updated YAML back to file
                with yaml_file.open("w", encoding="utf-8") as handle:
                    yaml.dump(
                        table_data,
                        handle,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False,
                    )

                # Update schema index with the new description
                self._update_schema_index(
                    schema_index_data, schema_name, table_name, rewritten_description
                )

                logger.info("Updated documentation in %s", yaml_file)
                successful += 1

                # Throttle to avoid rate limits
                time.sleep(10)

            except Exception as exc:
                from app.utils.logger import sanitize_for_log

                logger.error(
                    "Failed to process %s: %s",
                    yaml_file,
                    sanitize_for_log(str(exc), max_len=500),
                    exc_info=True,
                )
                failed += 1
                continue

        # Save updated schema index
        self._save_schema_index(schema_index_path, schema_index_data)

        # Summary logging
        logger.info(
            "Schema documentation complete: %d successful, %d failed out of %d total",
            successful,
            failed,
            len(yaml_files),
        )

        return SchemaDocumentationSummary(
            tables_total=len(yaml_files),
            documented=successful,
            failed=failed,
        )

    def _load_schema_index_data(self, index_path: Path) -> dict[str, Any]:
        if not index_path.exists():
            logger.warning("schema_index.yaml missing at %s", index_path)
            return {}

        try:
            with index_path.open("r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}
        except Exception as exc:
            from app.utils.logger import sanitize_for_log

            logger.error("Failed to load schema index: %s", sanitize_for_log(str(exc), max_len=500))
            return {}

    def _build_index_map(
        self, index_data: dict[str, Any]
    ) -> Mapping[tuple[str, str], dict[str, Any]]:
        table_map: dict[tuple[str, str], dict[str, Any]] = {}
        for table_entry in index_data.get("tables", []):
            schema = table_entry.get("schema", "").lower()
            table = table_entry.get("table", "").lower()
            if schema and table:
                table_map[(schema, table)] = table_entry
        return table_map

    def _update_schema_index(
        self, index_data: dict[str, Any], schema_name: str, table_name: str, description: str
    ) -> None:
        """Update the short_description in the in-memory index data."""
        if not description:
            return

        target_schema = schema_name.lower()
        target_table = table_name.lower()

        # Ensure tables list exists
        if "tables" not in index_data:
            index_data["tables"] = []

        # Try to find existing entry
        found = False
        for entry in index_data["tables"]:
            if (
                entry.get("schema", "").lower() == target_schema
                and entry.get("table", "").lower() == target_table
            ):
                entry["short_description"] = description
                found = True
                break

        # If not found, could add it, but usually we only update existing entries
        # to avoid messing up the structure if it's strictly managed.
        # For now, we only update if found to be safe, or we could append.
        # Let's append if not found, as that keeps it in sync.
        if not found:
            index_data["tables"].append(
                {"schema": schema_name, "table": table_name, "short_description": description}
            )

    def _save_schema_index(self, index_path: Path, index_data: dict[str, Any]) -> None:
        """Write the updated schema index back to disk."""
        if not index_data:
            return

        try:
            with index_path.open("w", encoding="utf-8") as handle:
                yaml.dump(
                    index_data,
                    handle,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            logger.info("Updated schema_index.yaml with new descriptions")
        except Exception as exc:
            from app.utils.logger import sanitize_for_log

            logger.error("Failed to save schema index: %s", sanitize_for_log(str(exc), max_len=500))

    def _table_description_from_index(
        self,
        schema_index: Mapping[tuple[str, str], dict[str, Any]],
        schema_name: str,
        table_name: str,
        fallback: str,
    ) -> str:
        lookup_key = (schema_name.lower(), table_name.lower())
        entry = schema_index.get(lookup_key)
        if entry:
            desc = entry.get("short_description")
            if desc:
                return desc
        return fallback

    def _intro_snippet(self, business_intro: str) -> str:
        """
        Return a cleaned-up version of the business intro, removing excessive blank lines and leading/trailing whitespace,
        but preserving the full content for LLM context.
        """
        if not business_intro:
            return ""
        # Remove lines that are only whitespace, but keep all actual content and structure
        lines = [line.rstrip() for line in business_intro.splitlines() if line.strip()]
        return "\n".join(lines).strip()

    def _combine_with_intro(self, description: str, intro_snippet: str) -> str:
        parts = []
        if description:
            parts.append(description.strip())
        if intro_snippet:
            parts.append(f"Context: {intro_snippet}")
        return "\n".join(parts)

    def _is_table_fully_documented(self, table_data: dict[str, Any]) -> bool:
        """Check if a table is already fully documented."""
        # Check table-level keywords
        if not table_data.get("keywords"):
            return False

        # Check columns
        for col in table_data.get("columns", []):
            if not col.get("description") or not col.get("keywords"):
                return False

        return True

    def _build_columns_payload(self, columns: list[dict[str, Any]]) -> str:
        payload = [
            {
                "name": col["name"],
                "type": col.get("sql_type", col.get("type", "unknown")),
                "is_nullable": col.get("is_nullable", True),
                "is_identity": col.get("is_identity", False),
            }
            for col in columns
        ]
        return json.dumps(payload, indent=2)

    def _truncate_text(
        self,
        text: str,
        max_chars: int,
        label: str,
        table_identifier: str,
    ) -> tuple[str, bool]:
        if not text:
            return "", False
        if len(text) <= max_chars:
            return text, False
        truncated = text[:max_chars]
        # Prefer to cut on a whitespace boundary to avoid mid-word breaks when possible
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        truncated = truncated.strip()
        logger.debug(
            "Truncated %s for %s from %d to %d characters",
            label,
            table_identifier,
            len(text),
            len(truncated),
        )
        return truncated, True

    def _apply_compact_prompt(
        self,
        original_prompt: dict[str, str],
        columns: list[dict[str, Any]],
    ) -> dict[str, str]:
        compact_prompt = dict(original_prompt)
        table_identifier = (
            f"{original_prompt.get('schema_name', '')}.{original_prompt.get('table_name', '')}"
        )
        compact_prompt["business_intro"] = ""
        compact_desc, _ = self._truncate_text(
            compact_prompt.get("table_description", ""),
            max_chars=1200,
            label="table_description_compact",
            table_identifier=table_identifier,
        )
        compact_prompt["table_description"] = compact_desc
        compact_columns = [
            {
                "name": col["name"],
                "type": col.get("sql_type", col.get("type", "unknown")),
            }
            for col in columns
        ]
        compact_prompt["columns_json"] = json.dumps(compact_columns, indent=2)
        logger.debug(
            "Applied compact prompt for %s: intro dropped, description %d chars, %d column summaries",
            table_identifier,
            len(compact_desc),
            len(compact_columns),
        )
        return compact_prompt


def document_database_schema(
    database_name: str,
    schema_output_dir: Path,
    intro_template_path: Path,
    provider: str | None = None,
    incremental: bool = True,
) -> SchemaDocumentationSummary:
    """Main entry point to document a database schema using LLM-generated descriptions.

    This function creates a SchemaDocumentingAgent and processes all table YAML files
    in the specified directory, generating business-friendly documentation for columns.

    Args:
        database_name: Name of the database (e.g., "crm_db"). Used for logging.
        schema_output_dir: Directory with generated schema YAML files to document
        intro_template_path: Path to business intro text file providing context
        provider: LLM provider to use. Defaults to auto-selection via get_llm.
        incremental: If True, skip tables that are already fully documented.

    Raises:
        FileNotFoundError: If schema_output_dir doesn't exist

    Example:
        >>> from pathlib import Path
        >>> document_database_schema(
        ...     database_name="crm_db",
        ...     schema_output_dir=Path("output/crm_db"),
        ...     intro_template_path=Path("config/prompts/crm_db_intro.txt"),
        ...     provider="groq"
        ... )
    """
    logger.info("Starting schema documentation for database: %s", database_name)
    logger.info("Using provider: %s", provider or "auto-select")

    try:
        # Initialize agent with specified configuration
        agent = SchemaDocumentingAgent(provider=provider)

        # Process all schema files
        summary = agent.document_schema(
            schema_output_dir, intro_template_path, incremental=incremental
        )

        logger.info("Schema documentation complete for %s", database_name)
        return summary

    except Exception as exc:
        logger.error("Schema documentation failed for %s: %s", database_name, exc, exc_info=True)
        raise


__all__ = [
    "ColumnDocumentation",
    "SchemaDocumentationSummary",
    "SchemaDocumentingAgent",
    "TableDocumentation",
    "document_database_schema",
]
