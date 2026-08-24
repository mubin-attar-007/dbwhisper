"""The worker process: ``python -m app.jobs.worker``.

Deliberately boring. It claims a job, runs it, records the outcome, and sleeps when there is nothing
to do. No threads, no async, no supervision tree - one job at a time per process, and you scale by
running more processes. That is enough for enrollment and evaluation workloads, and it means a
failure mode is a single stack trace rather than an interleaving.

It stops cleanly on SIGINT/SIGTERM: the job in flight finishes its current stage, the lease is
released, and the next worker resumes from the following stage.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from types import FrameType

from app.core.config import get_settings
from app.jobs import queue as jobq
from app.jobs import runner
from app.jobs.handlers import register_all
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


class Worker:
    def __init__(
        self,
        connection_string: str,
        *,
        kinds: list[str] | None = None,
        poll_interval: float = 2.0,
        max_jobs: int | None = None,
    ) -> None:
        self.connection_string = connection_string
        self.kinds = kinds
        self.poll_interval = poll_interval
        self.max_jobs = max_jobs
        self.identity = jobq.worker_identity()
        self._stopping = False
        self.jobs_done = 0

    def request_stop(self, *_args: object) -> None:
        if not self._stopping:
            logger.info("Worker %s stopping after the current job.", self.identity)
        self._stopping = True

    def _session(self):
        from db.database_manager import get_session

        return get_session(self.connection_string)

    def run_once(self) -> bool:
        """Claim and run at most one job. Returns True when a job was processed."""
        session = self._session()
        try:
            job = jobq.claim(session, worker=self.identity, kinds=self.kinds)
            if job is None:
                return False

            logger.info("Worker %s claimed %s job %s", self.identity, job.kind, job.job_id)
            try:
                context = runner.run_job(session, job)
            except runner.StageFailed as exc:
                jobq.fail(session, job, str(exc), retryable=exc.retryable)
                logger.warning("Job %s failed: %s", job.job_id, exc)
            except Exception as exc:  # pragma: no cover - defensive
                jobq.fail(session, job, f"{type(exc).__name__}: {exc}")
                logger.exception("Job %s crashed", job.job_id)
            else:
                summary = {name: output for name, output in context.items() if output}
                jobq.finish(session, job, result=summary)
                logger.info("Job %s finished", job.job_id)
            self.jobs_done += 1
            return True
        finally:
            session.close()

    def run(self) -> int:
        register_all()
        logger.info(
            "Worker %s started (kinds=%s, handlers=%s)",
            self.identity,
            self.kinds or "all",
            runner.registered_kinds(),
        )
        while not self._stopping:
            try:
                worked = self.run_once()
            except Exception:  # pragma: no cover - never let the loop die
                logger.exception("Worker loop error; continuing")
                worked = False

            if self.max_jobs is not None and self.jobs_done >= self.max_jobs:
                logger.info("Worker %s reached its job limit.", self.identity)
                break
            if not worked:
                # Sleep in short slices so a stop signal is noticed promptly.
                slept = 0.0
                while slept < self.poll_interval and not self._stopping:
                    time.sleep(min(0.25, self.poll_interval - slept))
                    slept += 0.25
        logger.info("Worker %s exited after %d job(s).", self.identity, self.jobs_done)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DBWhisper background worker")
    parser.add_argument("--kinds", nargs="*", help="Only process these job kinds")
    parser.add_argument("--poll-interval", type=float, default=None)
    parser.add_argument("--max-jobs", type=int, default=None, help="Exit after N jobs (for tests)")
    args = parser.parse_args(argv)

    settings = get_settings()
    connection_string = settings.postgres_connection_string
    if not connection_string:
        logger.error("POSTGRES_CONNECTION_STRING is not set; the worker has no queue to read.")
        return 2

    jobq.ensure_tables(connection_string)
    worker = Worker(
        connection_string,
        kinds=args.kinds,
        poll_interval=args.poll_interval or settings.worker_poll_interval_seconds,
        max_jobs=args.max_jobs,
    )
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handler(worker))
    return worker.run()


def _handler(worker: Worker):
    def handle(_signum: int, _frame: FrameType | None) -> None:
        worker.request_stop()

    return handle


if __name__ == "__main__":  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())


__all__ = ["Worker", "main"]
