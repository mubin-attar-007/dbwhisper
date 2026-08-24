"""The durable job queue: leases, resumable stages, and a worker that can be killed safely."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.jobs import queue as jobq
from app.jobs.models import Job, JobStatus
from app.jobs.runner import JobHandler, Stage, StageFailed, progress, register, run_job
from app.jobs.worker import Worker
from db.database_manager import create_metadata_tables, get_session


@pytest.fixture
def db(tmp_path) -> str:
    url = f"sqlite:///{tmp_path.as_posix()}/jobs.db"
    create_metadata_tables(url)
    jobq.ensure_tables(url)
    return url


@pytest.fixture
def session(db: str):
    s = get_session(db)
    yield s
    s.close()


class TestQueueLifecycle:
    def test_enqueue_then_claim_then_finish(self, session):
        job = jobq.enqueue(session, kind="reembed", source_id="demo", payload={"source_id": "demo"})
        assert job.status == JobStatus.QUEUED.value

        claimed = jobq.claim(session, worker="w1")
        assert claimed is not None and claimed.job_id == job.job_id
        assert claimed.status == JobStatus.RUNNING.value
        assert claimed.locked_by == "w1" and claimed.attempts == 1

        jobq.finish(session, claimed, result={"documents": 12})
        assert claimed.status == JobStatus.SUCCEEDED.value
        assert claimed.result == {"documents": 12}
        assert claimed.locked_by is None and claimed.finished_at is not None

    def test_an_empty_queue_claims_nothing(self, session):
        assert jobq.claim(session, worker="w1") is None

    def test_a_claimed_job_is_not_handed_out_twice(self, session):
        jobq.enqueue(session, kind="reembed", payload={})
        first = jobq.claim(session, worker="w1")
        second = jobq.claim(session, worker="w2")
        assert first is not None
        assert second is None, "two workers must never hold the same job"

    def test_kind_filtering(self, session):
        jobq.enqueue(session, kind="enroll", payload={})
        assert jobq.claim(session, worker="w1", kinds=["reembed"]) is None
        assert jobq.claim(session, worker="w1", kinds=["enroll"]) is not None

    def test_fifo_order(self, session):
        first = jobq.enqueue(session, kind="reembed", payload={"n": 1})
        jobq.enqueue(session, kind="reembed", payload={"n": 2})
        assert jobq.claim(session, worker="w1").job_id == first.job_id

    def test_queue_depth(self, session):
        assert jobq.queue_depth(session) == 0
        jobq.enqueue(session, kind="reembed", payload={})
        jobq.enqueue(session, kind="reembed", payload={})
        assert jobq.queue_depth(session) == 2
        jobq.claim(session, worker="w1")
        assert jobq.queue_depth(session) == 1


class TestFailureAndRetry:
    def test_a_retryable_failure_returns_the_job_to_the_queue(self, session):
        jobq.enqueue(session, kind="reembed", payload={}, max_attempts=3)
        job = jobq.claim(session, worker="w1")
        jobq.fail(session, job, "transient network error")
        assert job.status == JobStatus.QUEUED.value
        assert job.locked_by is None
        assert jobq.claim(session, worker="w2") is not None, "it must be claimable again"

    def test_attempts_are_exhausted_eventually(self, session):
        jobq.enqueue(session, kind="reembed", payload={}, max_attempts=2)
        for _ in range(2):
            job = jobq.claim(session, worker="w1")
            assert job is not None
            jobq.fail(session, job, "still broken")
        assert job.status == JobStatus.FAILED.value
        assert jobq.claim(session, worker="w1") is None

    def test_a_non_retryable_failure_stops_immediately(self, session):
        jobq.enqueue(session, kind="reembed", payload={}, max_attempts=5)
        job = jobq.claim(session, worker="w1")
        jobq.fail(session, job, "the data source does not exist", retryable=False)
        assert job.status == JobStatus.FAILED.value
        assert jobq.claim(session, worker="w1") is None

    def test_cancelling(self, session):
        job = jobq.enqueue(session, kind="reembed", payload={})
        assert jobq.cancel(session, job.job_id) is True
        assert jobq.claim(session, worker="w1") is None
        assert jobq.cancel(session, job.job_id) is False, "a terminal job cannot be cancelled again"


class TestLeases:
    def test_a_dead_workers_job_becomes_claimable(self, session):
        jobq.enqueue(session, kind="reembed", payload={}, max_attempts=5)
        job = jobq.claim(session, worker="dead-worker")
        assert job is not None

        # Simulate the worker dying: the lease is left behind and goes stale.
        job.locked_at = datetime.now(UTC) - timedelta(hours=2)
        session.commit()

        recovered = jobq.claim(session, worker="live-worker", lease_seconds=60)
        assert recovered is not None and recovered.job_id == job.job_id
        assert recovered.locked_by == "live-worker"
        assert recovered.attempts == 2

    def test_a_live_lease_is_respected(self, session):
        jobq.enqueue(session, kind="reembed", payload={}, max_attempts=5)
        jobq.claim(session, worker="w1")
        assert jobq.claim(session, worker="w2", lease_seconds=3600) is None

    def test_heartbeat_extends_the_lease(self, session):
        jobq.enqueue(session, kind="reembed", payload={}, max_attempts=5)
        job = jobq.claim(session, worker="w1")
        job.locked_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
        jobq.heartbeat(session, job)
        assert jobq.claim(session, worker="w2", lease_seconds=60) is None


class TestResumableStages:
    def _handler(self, calls: list[str], fail_on: str | None = None) -> JobHandler:
        def make(name: str):
            def run(_payload, _context):
                calls.append(name)
                if name == fail_on:
                    raise StageFailed(f"{name} exploded")
                return {"detail": f"{name} done", "value": name}

            return run

        return JobHandler(
            kind="test-resume",
            stages=[
                Stage("one", make("one")),
                Stage("two", make("two")),
                Stage("three", make("three")),
            ],
        )

    def test_all_stages_run_in_order(self, session):
        calls: list[str] = []
        register(self._handler(calls))
        job = jobq.enqueue(session, kind="test-resume", payload={})
        context = run_job(session, jobq.claim(session, worker="w1"))
        assert calls == ["one", "two", "three"]
        assert context["two"]["value"] == "two"
        assert [s.status for s in jobq.stages_for(session, job.job_id)] == [
            JobStatus.SUCCEEDED.value
        ] * 3

    def test_a_completed_stage_is_never_run_twice(self, session):
        calls: list[str] = []
        register(self._handler(calls, fail_on="three"))
        jobq.enqueue(session, kind="test-resume", payload={}, max_attempts=5)

        job = jobq.claim(session, worker="w1")
        with pytest.raises(StageFailed):
            run_job(session, job)
        assert calls == ["one", "two", "three"]
        jobq.fail(session, job, "stage three failed")

        # Second attempt: the expensive earlier stages are skipped.
        calls.clear()
        register(self._handler(calls))
        job = jobq.claim(session, worker="w2")
        run_job(session, job)
        assert calls == ["three"], "only the failed stage should re-run"

    def test_a_conditional_stage_is_marked_skipped(self, session):
        calls: list[str] = []

        def run(_payload, _context):
            calls.append("optional")
            return {}

        register(
            JobHandler(
                kind="test-optional",
                stages=[Stage("optional", run, when=lambda p: bool(p.get("do_it")))],
            )
        )
        job = jobq.enqueue(session, kind="test-optional", payload={"do_it": False})
        run_job(session, jobq.claim(session, worker="w1"))
        assert calls == []
        stage = jobq.stages_for(session, job.job_id)[0]
        assert stage.skipped is True and stage.status == JobStatus.SUCCEEDED.value

    def test_an_unregistered_kind_fails_without_retrying(self, session):
        jobq.enqueue(session, kind="no-such-handler", payload={})
        job = jobq.claim(session, worker="w1")
        with pytest.raises(StageFailed) as exc_info:
            run_job(session, job)
        assert exc_info.value.retryable is False

    def test_progress_reporting(self, session):
        calls: list[str] = []
        register(self._handler(calls, fail_on="three"))
        job = jobq.enqueue(session, kind="test-resume", payload={}, max_attempts=5)
        with pytest.raises(StageFailed):
            run_job(session, jobq.claim(session, worker="w1"))

        report = progress(session, job.job_id)
        assert report["stages_total"] == 3
        assert report["stages_done"] == 2
        assert report["percent"] == 67
        assert [s["name"] for s in report["stages"]] == ["one", "two", "three"]
        assert report["stages"][2]["status"] == JobStatus.FAILED.value


class TestWorker:
    def test_runs_queued_jobs_and_stops_at_the_limit(self, db: str, session):
        seen: list[str] = []
        register(
            JobHandler(
                kind="test-worker",
                stages=[Stage("only", lambda payload, _c: seen.append(payload["n"]) or {})],
            )
        )
        for n in ("a", "b", "c"):
            jobq.enqueue(session, kind="test-worker", payload={"n": n})

        worker = Worker(db, kinds=["test-worker"], poll_interval=0.01, max_jobs=2)
        worker.run()
        assert seen == ["a", "b"]
        assert jobq.queue_depth(session) == 1

    def test_an_idle_worker_stops_when_asked(self, db: str):
        worker = Worker(db, poll_interval=0.05)
        worker.request_stop()
        assert worker.run() == 0
        assert worker.jobs_done == 0

    def test_a_failing_job_does_not_kill_the_worker(self, db: str, session):
        register(
            JobHandler(
                kind="test-explode",
                stages=[Stage("boom", lambda _p, _c: (_ for _ in ()).throw(RuntimeError("nope")))],
            )
        )
        jobq.enqueue(session, kind="test-explode", payload={}, max_attempts=1)
        worker = Worker(db, kinds=["test-explode"], poll_interval=0.01, max_jobs=1)
        worker.run()

        job = session.query(Job).filter_by(kind="test-explode").first()
        session.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "nope" in job.error

    def test_run_once_reports_whether_it_worked(self, db: str, session):
        register(JobHandler(kind="test-once", stages=[Stage("only", lambda _p, _c: {})]))
        worker = Worker(db, kinds=["test-once"], poll_interval=0.01)
        assert worker.run_once() is False
        jobq.enqueue(session, kind="test-once", payload={})
        assert worker.run_once() is True


class TestEnrollHandler:
    def test_the_stage_list_is_the_documentation(self):
        from app.jobs.handlers import register_all
        from app.jobs.runner import get_handler

        register_all()
        assert get_handler("enroll").stage_names() == [
            "verify_read_only",
            "snapshot_schema",
            "extract_schema",
            "detect_drift",
            "document_schema",
            "build_index",
            "mark_enrolled",
        ]
        assert get_handler("reembed").stage_names() == ["build_index"]

    def test_a_payload_without_a_source_fails_fast(self, session):
        from app.jobs.handlers import register_all

        register_all()
        jobq.enqueue(session, kind="reembed", payload={})
        with pytest.raises(StageFailed) as exc_info:
            run_job(session, jobq.claim(session, worker="w1"))
        assert exc_info.value.retryable is False
