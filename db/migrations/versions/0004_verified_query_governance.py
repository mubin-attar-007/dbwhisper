"""Give verified queries a lifecycle instead of just existing.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-24

A saved question/SQL pair is only trustworthy relative to the schema it was approved against. Before
this, a pair survived a schema change unchanged and kept being offered to the model as a verified
example - which is how a "verified" example becomes a source of wrong answers. Recording the
snapshot and a literal-independent fingerprint lets drift mark a pair `stale` rather than silently
leaving it in circulation.

Existing rows are backfilled to `approved`, which preserves today's behaviour exactly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("status", sa.String(16), "approved"),
    ("snapshot_id", sa.String(128), None),
    ("sql_fingerprint", sa.String(64), None),
    ("tables", sa.JSON(), None),
    ("dialect", sa.String(32), None),
    ("reviewer", sa.String(320), None),
    ("reviewed_at", sa.DateTime(timezone=True), None),
    ("usage_count", sa.Integer(), "0"),
    ("last_validated_at", sa.DateTime(timezone=True), None),
    ("staleness_reason", sa.Text(), None),
    ("prompt_version", sa.String(64), None),
    ("model_profile", sa.String(64), None),
)


def _existing() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "verified_queries" not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns("verified_queries")}


def upgrade() -> None:
    existing = _existing()
    if not existing:
        return
    with op.batch_alter_table("verified_queries") as batch:
        for name, type_, default in _COLUMNS:
            if name not in existing:
                batch.add_column(sa.Column(name, type_, nullable=True, server_default=default))


def downgrade() -> None:
    existing = _existing()
    if not existing:
        return
    with op.batch_alter_table("verified_queries") as batch:
        for name, _type, _default in _COLUMNS:
            if name in existing:
                batch.drop_column(name)
