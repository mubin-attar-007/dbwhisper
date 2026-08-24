"""A queue in the application database, so the core product needs no broker.

Redis or a real broker would be the obvious choice, and both are supported deployments - but neither
may be *required*, because the brief is explicit that the core product must run with nothing paid and
nothing extra. A table plus a lease is enough for a workload measured in jobs per hour.

Claiming is where the correctness lives:

* on **PostgreSQL** a claim is ``SELECT ... FOR UPDATE SKIP LOCKED``, so several workers can compete
  without blocking each other or handing the same job out twice;
* on **SQLite** (single-writer anyway) it is a guarded conditional update, which is equivalent given
  the concurrency SQLite supports.

A lease, not a lock: a worker that dies mid-job leaves ``locked_at`` in the past, and the job becomes
claimable again once the lease expires. That is what makes a crash recoverable without an operator.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.jobs.models import Job, JobKind, JobStage, JobStatus

logger = logging.getLogger(__name__)

#: How long a worker may hold a job before another may take it over.
DEFAULT_LEASE_SECONDS = 900


def worker_identity() -> str:
    """Identifies the process holding a lease, for diagnosis rather than for correctness."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def enqueue(
    session: Session,
    *,
    kind: JobKind | str,
    payload: dict[str, Any] | None = None,
    source_id: str | None = None,
    owner_id: int | None = None,
    max_attempts: int = 3,
    job_id: str | None = None,
) -> Job:
    """Add a job. The payload must be JSON-safe and must not contain a credential."""
    job = Job(
        job_id=job_id or f"job-{uuid.uuid4().hex[:16]}",
        kind=str(kind),
        status=JobStatus.QUEUED.value,
        source_id=source_id,
        owner_id=owner_id,
        payload=payload or {},
        max_attempts=max_attempts,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    logger.info("Enqueued %s job %s", job.kind, job.job_id)
    return job


def claim(
    session: Session,
    *,
    worker: str,
    kinds: list[str] | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Job | None:
    """Take ownership of one runnable job, or return ``None`` if there is nothing to do."""
    now = datetime.now(UTC)
    expiry = now - timedelta(seconds=lease_seconds)
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"

    if dialect == "postgresql":
        return _claim_postgres(session, worker, kinds, now, expiry)
    return _claim_generic(session, worker, kinds, now, expiry)


def _claimable(query, kinds: list[str] | None, expiry: datetime):
    from sqlalchemy import or_

    query = query.filter(
        or_(
            Job.status == JobStatus.QUEUED.value,
            # A running job whose lease has expired: its worker is gone.
            (Job.status == JobStatus.RUNNING.value) & (Job.locked_at < expiry),
        )
    ).filter(Job.attempts < Job.max_attempts)
    if kinds:
        query = query.filter(Job.kind.in_(kinds))
    return query.order_by(Job.id.asc())


def _claim_postgres(session, worker, kinds, now, expiry) -> Job | None:
    job = _claimable(session.query(Job), kinds, expiry).with_for_update(skip_locked=True).first()
    if job is None:
        return None
    _mark_running(session, job, worker, now)
    return job


def _claim_generic(session, worker, kinds, now, expiry) -> Job | None:
    """Guarded update: re-check the state we selected on, so two workers cannot both win."""
    while True:
        job = _claimable(session.query(Job), kinds, expiry).first()
        if job is None:
            return None
        previous_status = job.status
        updated = (
            session.query(Job)
            .filter(Job.id == job.id, Job.status == previous_status)
            .update(
                {
                    "status": JobStatus.RUNNING.value,
                    "locked_by": worker,
                    "locked_at": now,
                    "started_at": job.started_at or now,
                    "attempts": Job.attempts + 1,
                },
                synchronize_session=False,
            )
        )
        session.commit()
        if updated:
            session.refresh(job)
            return job
        # Someone else took it between the select and the update; look for another.


def _mark_running(session: Session, job: Job, worker: str, now: datetime) -> None:
    job.status = JobStatus.RUNNING.value
    job.locked_by = worker
    job.locked_at = now
    job.started_at = job.started_at or now
    job.attempts = (job.attempts or 0) + 1
    session.commit()


def heartbeat(session: Session, job: Job) -> None:
    """Extend the lease during a long stage so it is not stolen mid-flight."""
    job.locked_at = datetime.now(UTC)
    session.commit()


def finish(session: Session, job: Job, *, result: dict[str, Any] | None = None) -> Job:
    job.status = JobStatus.SUCCEEDED.value
    job.result = result or {}
    job.error = None
    job.finished_at = datetime.now(UTC)
    job.locked_by = None
    session.commit()
    return job


def fail(session: Session, job: Job, error: str, *, retryable: bool = True) -> Job:
    """Release a failed job. It returns to the queue only if attempts remain."""
    exhausted = not retryable or (job.attempts or 0) >= (job.max_attempts or 1)
    job.status = JobStatus.FAILED.value if exhausted else JobStatus.QUEUED.value
    job.error = error[:2000]
    job.locked_by = None
    job.locked_at = None
    if exhausted:
        job.finished_at = datetime.now(UTC)
    session.commit()
    return job


def cancel(session: Session, job_id: str) -> bool:
    job = session.query(Job).filter_by(job_id=job_id).first()
    if job is None or JobStatus(job.status).is_terminal:
        return False
    job.status = JobStatus.CANCELLED.value
    job.finished_at = datetime.now(UTC)
    job.locked_by = None
    session.commit()
    return True


def get_job(session: Session, job_id: str) -> Job | None:
    return session.query(Job).filter_by(job_id=job_id).first()


def stages_for(session: Session, job_id: str) -> list[JobStage]:
    return (
        session.query(JobStage)
        .filter_by(job_id=job_id)
        .order_by(JobStage.position.asc(), JobStage.id.asc())
        .all()
    )


def queue_depth(session: Session) -> int:
    return session.query(Job).filter(Job.status == JobStatus.QUEUED.value).count()


def ensure_tables(connection_string: str) -> None:
    """Create the job tables if a deployment has not run migrations yet."""
    from db.database_manager import get_engine

    engine = get_engine(connection_string)
    Job.__table__.create(bind=engine, checkfirst=True)
    JobStage.__table__.create(bind=engine, checkfirst=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "cancel",
    "claim",
    "enqueue",
    "ensure_tables",
    "fail",
    "finish",
    "get_job",
    "heartbeat",
    "queue_depth",
    "stages_for",
    "worker_identity",
]
