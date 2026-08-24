"""Turning a caller-supplied data-source identifier into a filesystem path, safely.

A ``db_flag`` arrives from a URL path, a query string or a JSON body, and six places in the codebase
interpolated it straight into ``database_schemas/<db_flag>/...``. A value of ``../../../etc`` walks
out of the directory, and on Windows so do ``..\\``, a drive letter and a UNC prefix. CodeQL flagged
this as ``py/path-injection`` on the v2 dependency module; the same shape existed in five other
files, which is the argument for one function rather than six guards.

Two independent checks, because either alone has a known bypass:

1. **The identifier must look like an identifier.** ASCII letters, digits, underscore and hyphen,
   1-64 characters. This is an allowlist, so nothing has to be enumerated as dangerous - separators,
   ``..``, NUL bytes, drive letters, UNC prefixes and Unicode look-alikes all simply fail to match.
2. **The resolved path must still be inside the root.** Symlinks and any platform-specific
   normalisation happen *after* step 1, so the containment check is what makes the guarantee
   observable rather than argued.

The value is rejected rather than sanitised. Silently rewriting ``../../etc`` into ``etc`` would
turn an attack into a confusing 404 against a data source the caller never named.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: What an enrolled data-source identifier may contain. Deliberately narrower than the filesystem
#: allows: every real db_flag in this project is a lowercase identifier.
SOURCE_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

SCHEMAS_DIRNAME = "database_schemas"


class UnsafeSourceId(ValueError):
    """The identifier could not be used as a path component."""


def validate_source_id(source_id: str) -> str:
    """Return ``source_id`` unchanged, or raise :class:`UnsafeSourceId`.

    Returning the value rather than a boolean means a caller cannot forget to use the result.
    """
    candidate = (source_id or "").strip()
    if not SOURCE_ID_PATTERN.match(candidate):
        # The rejected value is not echoed: it is attacker-controlled and this message reaches logs.
        raise UnsafeSourceId(
            "A data source identifier must be 1-64 characters of letters, digits, underscore or "
            "hyphen."
        )
    return candidate


def schemas_root(root: Path | None = None) -> Path:
    return (root or PROJECT_ROOT) / SCHEMAS_DIRNAME


def source_dir(source_id: str, root: Path | None = None) -> Path:
    """``database_schemas/<source_id>``, proven to be inside ``database_schemas``."""
    base = schemas_root(root).resolve()
    candidate = (base / validate_source_id(source_id)).resolve()
    # Belt and braces: step 1 already makes traversal impossible, but a symlinked
    # database_schemas/<name> would still resolve outside, and that is worth catching here rather
    # than discovering it from a file read.
    if candidate != base and base not in candidate.parents:
        raise UnsafeSourceId("The resolved data source directory is outside the schema root.")
    return candidate


def schema_dir(source_id: str, root: Path | None = None) -> Path:
    """``database_schemas/<source_id>/schema`` - where extraction writes its artefacts."""
    return source_dir(source_id, root) / "schema"


def schema_index_path(source_id: str, root: Path | None = None) -> Path:
    """``database_schemas/<source_id>/schema/schema_index.yaml``."""
    return schema_dir(source_id, root) / "schema_index.yaml"


def intro_path(source_id: str, root: Path | None = None) -> Path:
    """``database_schemas/<source_id>/db_intro/<source_id>_intro.txt``."""
    safe = validate_source_id(source_id)
    return source_dir(safe, root) / "db_intro" / f"{safe}_intro.txt"


def is_safe_source_id(source_id: str) -> bool:
    """Non-raising form, for callers that want to fall back rather than fail."""
    try:
        validate_source_id(source_id)
    except UnsafeSourceId:
        return False
    return True


__all__ = [
    "PROJECT_ROOT",
    "SCHEMAS_DIRNAME",
    "SOURCE_ID_PATTERN",
    "UnsafeSourceId",
    "intro_path",
    "is_safe_source_id",
    "schema_dir",
    "schema_index_path",
    "schemas_root",
    "source_dir",
    "validate_source_id",
]
