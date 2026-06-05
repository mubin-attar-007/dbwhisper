"""YAML generation helpers for schema extraction pipeline."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.models import DatabaseSchemaArtifacts
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


class YamlSchemaWriter:
    """Persist schema artifacts as YAML files on disk."""

    def __init__(
        self, output_dir: Path, backup_existing: bool = True, merge_existing: bool = True
    ) -> None:
        self.output_dir = output_dir
        self.backup_existing = backup_existing
        self.merge_existing = merge_existing
        self._prepared = False

    def write(self, artifacts: DatabaseSchemaArtifacts) -> Path:
        if not self._prepared:
            self._prepare_output_dir()
            self._prepared = True

        for schema_name, bucket in artifacts.schemas.items():
            schema_dir = self.output_dir / schema_name
            # View YAML output removed; we export only table files

            for table_name, table_data in bucket["tables"].items():
                self._dump_yaml(schema_dir / f"{table_name}.yaml", table_data)
            # no view outputs

        self._dump_yaml(self.output_dir / "metadata.yaml", artifacts.metadata_summary)
        self._dump_yaml(self.output_dir / "schema_index.yaml", artifacts.schema_index)
        logger.info("Schema YAML written to %s", self.output_dir)
        return self.output_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare_output_dir(self) -> None:
        if self.output_dir.exists():
            # If we are merging, we don't want to wipe the directory.
            # We only backup if we are NOT merging (i.e. full overwrite) OR if explicitly requested.
            # However, the user requirement implies we want to keep the files in place to merge them.
            # So if merge_existing is True, we skip the backup/wipe step for the directory itself,
            # relying on _dump_yaml to handle individual file merging.
            if self.merge_existing:
                return

            if self.backup_existing:
                timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                backup_dir = self.output_dir.with_name(f"{self.output_dir.name}_backup_{timestamp}")
                shutil.move(self.output_dir, backup_dir)
                logger.info("Existing schema output moved to %s", backup_dir)
            else:
                shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _dump_yaml(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        final_payload = payload
        if self.merge_existing and path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    existing_data = yaml.safe_load(handle)
                if isinstance(existing_data, dict) and isinstance(payload, dict):
                    final_payload = self._merge_payloads(existing_data, payload)
            except Exception as e:
                from app.utils.logger import sanitize_for_log

                logger.warning(
                    "Failed to merge existing YAML at %s: %s",
                    path,
                    sanitize_for_log(str(e), max_len=300),
                )

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        sanitized = self._sanitize_for_yaml(final_payload)
        with tmp_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(
                sanitized, handle, sort_keys=False, allow_unicode=True, default_flow_style=False
            )
        tmp_path.replace(path)

    def _merge_payloads(self, existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        """Merge new schema structure with existing documentation."""
        merged = new.copy()

        # Preserve table-level documentation
        if "description" in existing:
            merged["description"] = existing["description"]
        if "keywords" in existing:
            merged["keywords"] = existing["keywords"]

        # Preserve column-level documentation
        if "columns" in new and "columns" in existing:
            # Create a map of existing columns for fast lookup
            existing_cols = {
                col.get("name"): col
                for col in existing["columns"]
                if isinstance(col, dict) and "name" in col
            }

            merged_cols = []
            for new_col in new["columns"]:
                col_name = new_col.get("name")
                if col_name in existing_cols:
                    existing_col = existing_cols[col_name]
                    # Copy over documentation fields
                    if "description" in existing_col:
                        new_col["description"] = existing_col["description"]
                    if "keywords" in existing_col:
                        new_col["keywords"] = existing_col["keywords"]
                merged_cols.append(new_col)
            merged["columns"] = merged_cols

        return merged

    def _sanitize_for_yaml(self, payload: dict[str, object]) -> dict[str, object]:
        def _sanitize(value: Any) -> Any:
            if isinstance(value, str):
                return str(value)
            if isinstance(value, dict):
                return {str(k): _sanitize(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_sanitize(item) for item in value]
            if isinstance(value, tuple):
                return [_sanitize(item) for item in value]
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            from app.utils.logger import sanitize_for_log

            logger.debug(
                "Sanitizing YAML value of unsupported type %s: %s",
                type(value),
                sanitize_for_log(value, max_len=200),
            )
            return str(value)

        return {str(k): _sanitize(v) for k, v in payload.items()}


__all__ = ["YamlSchemaWriter"]
