"""Add Database_config.connection_secret for encrypted connection strings.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-24

The legacy plaintext ``connection_string`` column is deliberately **kept** by this migration. Two
reasons: a deployment without ``DBW_SECRET_KEYS`` must keep working while the operator generates a
key, and leaving the old column means this revision can be rolled back without losing the ability
to connect. A later migration drops it once deployments have moved.

Existing rows are not encrypted here - that needs the key, which lives in the environment and not in
the migration. Run ``app.platform.connection_secrets.encrypt_existing_rows`` (invoked at startup
when a key is present) to move them across.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "connection_secret" not in _columns("Database_config"):
        with op.batch_alter_table("Database_config") as batch:
            batch.add_column(sa.Column("connection_secret", sa.Text(), nullable=True))


def downgrade() -> None:
    if "connection_secret" in _columns("Database_config"):
        with op.batch_alter_table("Database_config") as batch:
            batch.drop_column("connection_secret")
