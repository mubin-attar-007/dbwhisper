"""Turning a :class:`~app.evaluation.datasets.spec.DatasetSpec` into the artefacts a run needs.

Three outputs, and the reason each one exists:

* **The SQLite file.** Not committed - ``*.sqlite`` is gitignored and a binary blob in a portfolio
  repository is a liability. It is rebuilt from the generator on demand, which is only defensible
  because the generator is deterministic; :func:`fixture_digest` is the test that keeps it so.
* **``database_schemas/<source_id>/schema/schema_index.yaml``.** The policy engine resolves a table
  against this file (``app/sqlpolicy/scope_loader.py``), and the graph's validate node passes
  ``require_scope=True``. Writing a real enrollment artefact is what makes an evaluation run go
  through the *real* allowlist rather than a relaxed evaluation-only path. Nothing here weakens the
  policy engine for the sake of the harness.
* **Retrieval documents.** Built from the same spec, so the retriever sees exactly the tables and
  columns the fixture has.

The writer is idempotent by content: when the YAML it would write is byte-identical to what is on
disk it leaves the file alone. That matters because ``scope_from_schema_index`` caches on mtime, so
a pointless rewrite would silently invalidate the cache on every single case.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from pathlib import Path

import yaml

from app.embeddings.base import EmbeddingProvider
from app.evaluation.datasets.spec import DatasetSpec
from app.retrieval.base import SearchDocument
from app.retrieval.documents import (
    column_document,
    relationship_document,
    table_document,
    verified_query_document,
)
from app.retrieval.memory_index import InMemoryRetrievalIndex
from app.user_db_config_loader import PROJECT_ROOT

logger = logging.getLogger(__name__)

#: Where generated ``.sqlite`` fixtures live. ``Temp/`` is gitignored, so a build leaves no trace in
#: the working tree. Override with ``DBW_EVAL_FIXTURE_DIR`` to build somewhere else (CI cache, tmpfs).
FIXTURE_DIR_ENV = "DBW_EVAL_FIXTURE_DIR"
DEFAULT_FIXTURE_DIR = PROJECT_ROOT / "Temp" / "eval-fixtures"


#: The snapshot id every document and every scope for a dataset shares. Derived from the dataset
#: version rather than a timestamp, so two builds of the same version index identically.
def snapshot_id(spec: DatasetSpec) -> str:
    return f"eval:{spec.name}@{spec.version}"


def fixture_dir() -> Path:
    override = os.environ.get(FIXTURE_DIR_ENV)
    return Path(override) if override else DEFAULT_FIXTURE_DIR


def fixture_path(spec: DatasetSpec, root: Path | None = None) -> Path:
    return (root or fixture_dir()) / f"{spec.name}-{spec.version}.sqlite"


def connection_string(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


# ---------------------------------------------------------------------------------------------
# SQLite fixture
# ---------------------------------------------------------------------------------------------


#: How many times to retry deleting a fixture that something still holds open.
_UNLINK_ATTEMPTS = 6
_UNLINK_BACKOFF_SECONDS = 0.05


def _remove_fixture(target: Path) -> bool:
    """Delete a built fixture, returning whether it worked.

    Windows will not unlink a file another handle has open, and the execution service caches one
    pooled engine per connection string - so a rebuild inside a process that has already run a query
    hits a ``PermissionError``. Dropping the engine cache releases the pooled DBAPI connection when
    the garbage collector gets to it, which is not synchronous, hence the short retry loop. If it
    still cannot be removed the caller rewrites the contents in place instead; nothing here fails a
    run over a file-locking detail.
    """
    import gc
    import time

    try:
        target.unlink()
        return True
    except PermissionError:
        pass

    try:
        from app.execution.connections import dispose_all

        dispose_all()
    except Exception:  # the execution package is optional for pure dataset work
        logger.debug("Could not drop the execution engine cache", exc_info=True)

    for attempt in range(_UNLINK_ATTEMPTS):
        gc.collect()
        try:
            target.unlink()
            return True
        except PermissionError:
            time.sleep(_UNLINK_BACKOFF_SECONDS * (attempt + 1))
    return False


def _write_fixture(spec: DatasetSpec, target: Path) -> None:
    """Create every table and insert every row into ``target``, replacing what is there."""
    population = spec.generate()
    connection = sqlite3.connect(target)
    try:
        # Pinned so the file bytes do not depend on the local SQLite build's defaults; without this,
        # "the same generator produces the same bytes" would not be a testable claim.
        connection.execute("PRAGMA page_size = 4096")
        connection.execute("PRAGMA encoding = 'UTF-8'")
        connection.execute("PRAGMA journal_mode = DELETE")
        for table in spec.tables:
            # DROP first so a rebuild works even when the platform will not let the file be
            # unlinked (see build_fixture). Harmless on a file that has just been created.
            connection.execute(f"DROP TABLE IF EXISTS {table.name}")
            connection.execute(table.ddl())
        for table in spec.tables:
            rows = population[table.name]
            if rows:
                connection.executemany(table.insert(), rows)
        connection.commit()
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()


def build_fixture(spec: DatasetSpec, path: Path | None = None, *, force: bool = False) -> Path:
    """Write the fixture and return its path. An existing file is reused unless ``force``.

    A rebuild prefers to replace the file outright. When the platform refuses - Windows will not
    unlink a file another handle still has open, and a pooled SQLAlchemy connection from an earlier
    run in the same process counts - it rewrites the contents in place instead. The *data* is
    identical either way; only SQLite's header counters differ, which is why
    :func:`fixture_digest` builds into a scratch directory rather than reusing this file.
    """
    target = path or fixture_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        return target
    if target.exists() and not _remove_fixture(target):
        logger.debug("%s is held open; rewriting its contents in place", target)
    _write_fixture(spec, target)
    return target


def fixture_digest(spec: DatasetSpec) -> str:
    """SHA-256 of a freshly built fixture. Two builds of the same spec must produce the same digest.

    Built into a scratch directory on purpose: the digest is a property of the generator and the
    writer, not of whatever state the canonical file happens to be in, and computing it must never
    disturb a fixture another part of the process is reading.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="dbw-eval-digest-") as scratch:
        target = Path(scratch) / f"{spec.name}-{spec.version}.sqlite"
        _write_fixture(spec, target)
        return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def row_counts(spec: DatasetSpec, path: Path | None = None) -> dict[str, int]:
    """Rows per table in the built fixture - printed in the report so ``n`` is never a mystery."""
    target = build_fixture(spec, path)
    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        # A table name cannot be a bind parameter in SQL, so it has to be interpolated. It is not
        # user input - it comes from this repository's own DatasetSpec literals - and the connection
        # is opened read-only (mode=ro) besides. The guard below asserts that rather than assuming
        # it, so the suppression on the next statement stays true if a spec ever takes a name from
        # somewhere else.
        for table in spec.tables:
            if not table.name.isidentifier():
                raise ValueError(
                    f"Refusing to interpolate an unexpected table name: {table.name!r}"
                )
        return {
            table.name: int(
                connection.execute(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0]  # nosec B608
            )
            for table in spec.tables
        }
    finally:
        connection.close()


def run_readonly(
    spec: DatasetSpec, sql: str, path: Path | None = None
) -> tuple[list[str], list[tuple]]:
    """Execute a statement against the fixture directly, for computing reference results.

    This bypasses :mod:`app.execution` on purpose: it is the *oracle*, and an oracle that went
    through the system under test could not detect a fault in it. It opens the file read-only and
    the caller is trusted with the SQL because the only SQL passed here is committed gold SQL.
    """
    target = build_fixture(spec, path)
    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        cursor = connection.execute(sql)
        columns = [description[0] for description in (cursor.description or [])]
        rows = [tuple(row) for row in cursor.fetchall()]
        return columns, rows
    finally:
        connection.close()


# ---------------------------------------------------------------------------------------------
# Enrollment artefact
# ---------------------------------------------------------------------------------------------


def schema_index_path(spec: DatasetSpec, root: Path | None = None) -> Path:
    base = root or (PROJECT_ROOT / "database_schemas")
    return base / spec.source_id / "schema" / "schema_index.yaml"


def write_schema_index(spec: DatasetSpec, root: Path | None = None) -> Path:
    """Write the enrollment artefact the policy engine reads, only when the content changed."""
    path = schema_index_path(spec, root)
    body = yaml.safe_dump(spec.schema_index(), sort_keys=False, allow_unicode=True)
    header = (
        "# GENERATED FILE - do not edit by hand.\n"
        f"# Written by app/evaluation/datasets/build.py for the synthetic evaluation dataset\n"
        f"# '{spec.name}' v{spec.version}. Rebuild with:  uv run python -m app.evaluation.cli build\n"
        "# The database it describes contains fabricated data only.\n"
    )
    content = header + body
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return path  # unchanged: leave the mtime alone or the scope cache thrashes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# Retrieval documents
# ---------------------------------------------------------------------------------------------


def build_documents(spec: DatasetSpec) -> list[SearchDocument]:
    """Table, column, relationship and verified-query documents for one dataset."""
    snapshot = snapshot_id(spec)
    documents: list[SearchDocument] = []
    for table in spec.tables:
        documents.append(
            table_document(
                source_id=spec.source_id,
                snapshot_id=snapshot,
                schema=spec.schema_name,
                table=table.name,
                description=table.description,
                columns=list(table.column_names),
                primary_key=list(table.primary_key),
                synonyms=list(table.keywords),
            )
        )
        documents.extend(
            column_document(
                source_id=spec.source_id,
                snapshot_id=snapshot,
                schema=spec.schema_name,
                table=table.name,
                column=column.name,
                data_type=column.sql_type,
                description=column.description,
                nullable=column.nullable,
            )
            for column in table.columns
        )
    documents.extend(
        relationship_document(
            source_id=spec.source_id,
            snapshot_id=snapshot,
            from_table=relationship.from_table,
            from_columns=list(relationship.from_columns),
            to_table=relationship.to_table,
            to_columns=list(relationship.to_columns),
        )
        for relationship in spec.relationships
    )
    documents.extend(
        verified_query_document(
            source_id=spec.source_id,
            snapshot_id=snapshot,
            question=question,
            sql=sql,
            pair_id=index,
        )
        for index, (question, sql) in enumerate(spec.verified_queries, start=1)
    )
    return documents


def build_index(
    spec: DatasetSpec, embedder: EmbeddingProvider | None = None
) -> InMemoryRetrievalIndex:
    """Index one dataset. Embeddings are best-effort: lexical search still works without them."""
    documents = build_documents(spec)
    if embedder is not None:
        try:
            result = embedder.embed_documents([d.searchable_text() for d in documents])
            for document, vector in zip(documents, result.vectors, strict=True):
                document.embedding = vector
                document.embedding_version = result.version.key
        except Exception:
            # A dead embedder degrades retrieval to lexical-only; it must not abort an eval run.
            # The report records which retrieval modes were live via the run's evidence.
            pass
    index = InMemoryRetrievalIndex()
    index.index(documents)
    return index


# ---------------------------------------------------------------------------------------------
# Everything at once
# ---------------------------------------------------------------------------------------------


def prepare(spec: DatasetSpec, *, force: bool = False) -> tuple[Path, Path]:
    """Build the fixture and write the enrollment artefact. Returns ``(fixture, schema_index)``."""
    return build_fixture(spec, force=force), write_schema_index(spec)


__all__ = [
    "DEFAULT_FIXTURE_DIR",
    "FIXTURE_DIR_ENV",
    "build_documents",
    "build_fixture",
    "build_index",
    "connection_string",
    "fixture_digest",
    "fixture_dir",
    "fixture_path",
    "prepare",
    "row_counts",
    "run_readonly",
    "schema_index_path",
    "snapshot_id",
    "write_schema_index",
]
