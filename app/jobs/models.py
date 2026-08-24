"""Durable jobs and their stages.

Enrollment is the reason this exists. It extracts a schema, writes YAML, calls a model once per
table and embeds the result - minutes of work that used to run inside the HTTP request, on the event
loop, with no record of how far it got. A worker restart lost everything and the client saw a
timeout.

So a job is a row, and so is each of its stages. Two consequences follow, and they are the whole
design:

* **Resumable.** A stage that already succeeded is skipped on the next attempt, so restarting a
  half-finished enrollment does not re-run the expensive model calls.
* **Observable.** Status, timing, attempt count and the last error live in the database, which is
  what lets an API return honest progress instead of "probably still going".
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)

from db.model import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class JobKind(StrEnum):
    ENROLL = "enroll"
    REEMBED = "reembed"
    DRIFT_SYNC = "drift_sync"
    EVALUATION = "evaluation"


class Job(Base):
    """One unit of background work, owned by at most one worker at a time."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, nullable=False, index=True)
    kind = Column(String(32), nullable=False, index=True)
    status = Column(String(16), nullable=False, default=JobStatus.QUEUED.value, index=True)
    #: Which data source this job is about, so progress can be shown next to it.
    source_id = Column(String(100), nullable=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: Job input. Never holds a credential - only the identifier needed to resolve one.
    payload = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    #: Set while a worker holds the job; cleared when it finishes or the lease expires.
    locked_by = Column(String(64), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "source_id": self.source_id,
            "attempts": self.attempts,
            "error": self.error,
            "result": self.result,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
        }


class JobStage(Base):
    """One resumable step of a job. Its ``name`` is the resume key."""

    __tablename__ = "job_stages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default=JobStatus.QUEUED.value)
    detail = Column(Text, nullable=True)
    duration_ms = Column(Float, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    #: Small, JSON-safe output a later stage may need (counts, paths, ids) - never rows or secrets.
    output = Column(JSON, nullable=True)
    skipped = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "position": self.position,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
            "output": self.output,
            "skipped": self.skipped,
            "updated_at": _iso(self.updated_at),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


__all__ = ["Job", "JobKind", "JobStage", "JobStatus"]
