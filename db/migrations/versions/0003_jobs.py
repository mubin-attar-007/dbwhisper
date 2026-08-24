"""Add the jobs and job_stages tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24

Enrollment moves out of the HTTP request and into a worker. A job is a row so it can be resumed
after a restart; a stage is a row so an already-completed step is never paid for twice.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "jobs" not in existing:
        op.create_table(
            "jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
            sa.Column("source_id", sa.String(100), nullable=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("locked_by", sa.String(64), nullable=True),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_jobs_job_id", "jobs", ["job_id"], unique=True)
        op.create_index("ix_jobs_status", "jobs", ["status"])
        op.create_index("ix_jobs_kind", "jobs", ["kind"])
        op.create_index("ix_jobs_source_id", "jobs", ["source_id"])

    if "job_stages" not in existing:
        op.create_table(
            "job_stages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("duration_ms", sa.Float(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output", sa.JSON(), nullable=True),
            sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_job_stages_job_id", "job_stages", ["job_id"])


def downgrade() -> None:
    for table in ("job_stages", "jobs"):
        if table in _tables():
            op.drop_table(table)
