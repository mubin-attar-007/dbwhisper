"""Baseline: the v1 application schema (users, user_sessions, Database_config, verified_queries).

Revision ID: 0001
Revises:
Create Date: 2026-08-21

Deployments that already ran ``Base.metadata.create_all`` have these tables; the baseline is
therefore **idempotent** - it creates only what is missing and repairs the one known drift
(``Database_config.owner_id`` added after multi-tenancy landed) so that ``alembic upgrade head`` is
safe on fresh and pre-existing databases alike.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _existing_tables()

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if "user_sessions" not in existing:
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    if "Database_config" not in existing:
        op.create_table(
            "Database_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("db_flag", sa.String(100), nullable=False),
            sa.Column("db_type", sa.String(50), nullable=False),
            sa.Column("connection_string", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("max_rows", sa.Integer(), nullable=False, server_default="1000"),
            sa.Column("query_timeout", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("intro_template", sa.Text(), nullable=True),
            sa.Column(
                "exclude_column_matches", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("schema_extracted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("schema_extraction_date", sa.DateTime(), server_default=sa.func.now()),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index("ix_Database_config_db_flag", "Database_config", ["db_flag"], unique=True)
        op.create_index("ix_Database_config_owner_id", "Database_config", ["owner_id"])
    elif "owner_id" not in _columns("Database_config"):
        with op.batch_alter_table("Database_config") as batch:
            batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))

    if "verified_queries" not in existing:
        op.create_table(
            "verified_queries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("db_flag", sa.String(100), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("sql", sa.Text(), nullable=False),
            sa.Column("embedding_id", sa.String(64), nullable=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_verified_queries_db_flag", "verified_queries", ["db_flag"])
        op.create_index("ix_verified_queries_owner_id", "verified_queries", ["owner_id"])


def downgrade() -> None:
    # The baseline is never rolled back automatically: it would destroy user data.
    raise RuntimeError("Refusing to downgrade below the baseline (would drop application data).")
