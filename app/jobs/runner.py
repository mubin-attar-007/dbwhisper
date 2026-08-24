"""Running a job as a sequence of resumable stages.

The contract a handler signs up to: declare an ordered list of named stages, and make each one
**idempotent**. In exchange, a stage that already succeeded is never run twice - which is what turns
"the worker died during enrollment" from "start over and pay for the model calls again" into "carry
on from stage 7".

A stage returns a small JSON-safe dict. Later stages read it, and the API shows it as progress.
Nothing large or secret belongs in there: it is persisted and displayed.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.jobs import queue as jobq
from app.jobs.models import Job, JobStage, JobStatus

logger = logging.getLogger(__name__)

#: A stage receives the job payload and everything earlier stages returned.
StageFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]


class StageFailed(RuntimeError):
    """A stage failed in a way that should stop the job. Carries whether a retry could help."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class Stage:
    name: str
    run: StageFn
    #: Stages that only make sense sometimes (embeddings when the caller asked for them).
    when: Callable[[dict[str, Any]], bool] | None = None


@dataclass(slots=True)
class JobHandler:
    kind: str
    stages: list[Stage] = field(default_factory=list)

    def stage_names(self) -> list[str]:
        return [s.name for s in self.stages]


_REGISTRY: dict[str, JobHandler] = {}


def register(handler: JobHandler) -> JobHandler:
    _REGISTRY[handler.kind] = handler
    return handler


def get_handler(kind: str) -> JobHandler | None:
    return _REGISTRY.get(kind)


def registered_kinds() -> list[str]:
    return sorted(_REGISTRY)


def _stage_row(session: Session, job_id: str, name: str, position: int) -> JobStage:
    row = session.query(JobStage).filter_by(job_id=job_id, name=name).first()
    if row is None:
        row = JobStage(job_id=job_id, name=name, position=position)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def run_job(session: Session, job: Job) -> dict[str, Any]:
    """Execute a job's stages in order, skipping those already done. Raises on failure."""
    handler = get_handler(job.kind)
    if handler is None:
        raise StageFailed(f"No handler is registered for job kind '{job.kind}'", retryable=False)

    payload = dict(job.payload or {})
    context: dict[str, Any] = {}

    for position, stage in enumerate(handler.stages):
        row = _stage_row(session, job.job_id, stage.name, position)

        if row.status == JobStatus.SUCCEEDED.value:
            # The resume path: this is what a restart is for.
            context[stage.name] = row.output or {}
            logger.info("Job %s: stage %s already done, skipping", job.job_id, stage.name)
            continue

        if stage.when is not None and not stage.when(payload):
            row.status = JobStatus.SUCCEEDED.value
            row.skipped = True
            row.detail = "not requested"
            row.updated_at = datetime.now(UTC)
            session.commit()
            context[stage.name] = {}
            continue

        row.status = JobStatus.RUNNING.value
        row.attempts = (row.attempts or 0) + 1
        row.updated_at = datetime.now(UTC)
        session.commit()

        started = time.perf_counter()
        try:
            output = stage.run(payload, context) or {}
        except StageFailed as exc:
            _record_failure(session, row, started, str(exc))
            raise
        except Exception as exc:
            _record_failure(session, row, started, f"{type(exc).__name__}: {exc}")
            logger.debug("Stage %s failed:\n%s", stage.name, traceback.format_exc())
            raise StageFailed(f"stage '{stage.name}' failed: {type(exc).__name__}: {exc}") from exc

        row.status = JobStatus.SUCCEEDED.value
        row.output = output
        row.detail = str(output.get("detail") or "")[:500] or None
        row.duration_ms = (time.perf_counter() - started) * 1000
        row.updated_at = datetime.now(UTC)
        session.commit()
        context[stage.name] = output

        jobq.heartbeat(session, job)

    return context


def _record_failure(session: Session, row: JobStage, started: float, message: str) -> None:
    row.status = JobStatus.FAILED.value
    row.detail = message[:500]
    row.duration_ms = (time.perf_counter() - started) * 1000
    row.updated_at = datetime.now(UTC)
    session.commit()


def progress(session: Session, job_id: str) -> dict[str, Any]:
    """What an API returns when asked how a job is going."""
    job = jobq.get_job(session, job_id)
    if job is None:
        return {}
    stages = [s.as_dict() for s in jobq.stages_for(session, job_id)]
    handler = get_handler(job.kind)
    total = len(handler.stages) if handler else len(stages)
    done = sum(1 for s in stages if s["status"] == JobStatus.SUCCEEDED.value)
    return {
        **job.as_dict(),
        "stages": stages,
        "stages_done": done,
        "stages_total": total,
        "percent": round(100 * done / total) if total else 0,
    }


__all__ = [
    "JobHandler",
    "Stage",
    "StageFailed",
    "get_handler",
    "progress",
    "register",
    "registered_kinds",
    "run_job",
]
