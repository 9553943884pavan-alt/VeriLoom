from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
import threading

import pytest

from app.jobs import worker
from app.jobs.queue import Job

ORG = UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = UUID("22222222-2222-4222-8222-222222222222")
RESOURCE = UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture(autouse=True)
def fake_session_events(monkeypatch):
    # FakeSession is not a SQLAlchemy event target. Dedicated tests below retain
    # the registered callback to exercise the same before_commit guard.
    monkeypatch.setattr(worker.event, "listen", lambda *args: None)
    monkeypatch.setattr(worker.event, "remove", lambda *args: None)


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def execute(self, statement, params):
        self.sql.append((str(statement), params))
        return type("Result", (), {"scalar_one_or_none": lambda self: JOB_ID})()


def test_run_once_commits_claim_before_handler_and_completes(monkeypatch):
    db = FakeSession()
    job = Job(JOB_ID, ORG, "review_revision", RESOURCE, "running", 1,
              datetime.now(timezone.utc), None, "worker-a", None)
    events = []
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: job)
    monkeypatch.setattr(worker, "fail_exhausted_leases", lambda *a, **kw: [])
    monkeypatch.setattr(worker, "complete_job", lambda *a, **kw: events.append(("complete", db.commits)) or True)
    monkeypatch.setattr(worker, "fail_job", lambda *a, **kw: None)
    assert worker.run_once(lambda: db, worker_id="worker-a", handlers={
        "review_revision": lambda session, claimed: events.append(("handler", session.commits)),
    })
    assert events == [("handler", 2), ("complete", 3)]
    assert db.commits == 4


def test_run_once_uses_safe_error_and_marks_ingestion_failed_at_retry_limit(monkeypatch):
    db = FakeSession()
    job = Job(JOB_ID, ORG, "ingest_source", RESOURCE, "running", 5,
              datetime.now(timezone.utc), None, "worker-a", None)
    captured = {}
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: job)
    monkeypatch.setattr(worker, "fail_exhausted_leases", lambda *a, **kw: [])
    monkeypatch.setattr(worker, "complete_job", lambda *a, **kw: False)
    monkeypatch.setattr(worker, "fail_job", lambda *a, **kw: captured.update(kw) or "failed")
    worker.run_once(lambda: db, worker_id="worker-a", handlers={
        "ingest_source": lambda *_: (_ for _ in ()).throw(ValueError("secret text should not leak")),
    })
    assert captured["error_code"] == "worker_unexpected_error"
    assert captured["retryable"] is True
    assert any("ingestion_status = 'failed'" in sql for sql, _ in db.sql)


def test_run_forever_rejects_unbounded_poll_interval():
    import pytest

    with pytest.raises(ValueError):
        worker.run_forever(lambda: FakeSession(), worker_id="w", stop_event=__import__("threading").Event(),
                           poll_seconds=61)


def test_worker_claims_only_kinds_with_registered_handlers(monkeypatch):
    db = FakeSession()
    captured = {}
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: captured.update(kw) or None)
    monkeypatch.setattr(worker, "fail_exhausted_leases", lambda *a, **kw: [])
    assert not worker.run_once(lambda: db, worker_id="worker-a", handlers={
        "ingest_source": lambda *_: None,
    })
    assert captured["allowed_kinds"] == ("ingest_source",)


def test_expired_final_review_attempt_marks_review_and_draft_failed(monkeypatch):
    db = FakeSession()
    expired = Job(JOB_ID, ORG, "review_revision", RESOURCE, "failed", 5,
                  datetime.now(timezone.utc), None, None, "attempts_exhausted")
    monkeypatch.setattr(worker, "fail_exhausted_leases", lambda *a, **kw: [expired])
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: None)
    assert not worker.run_once(lambda: db, worker_id="worker-a", handlers={
        "review_revision": lambda *_: None,
    })
    assert any("UPDATE reviews SET state = 'failed'" in sql for sql, _ in db.sql)
    assert any("UPDATE drafts AS d SET state = 'review_failed'" in sql for sql, _ in db.sql)


@pytest.mark.parametrize("outcome", [True, False, "error"])
def test_heartbeat_independent_session_cleanup_and_lost_commit_guard(monkeypatch, outcome):
    handler_db, heartbeat_db = FakeSession(), FakeSession()
    renewed = threading.Event()
    callbacks = []
    monkeypatch.setattr(worker.event, "listen", lambda db, name, callback: callbacks.append(callback))
    monkeypatch.setattr(worker.event, "remove", lambda db, name, callback: callbacks.remove(callback))
    job = Job(JOB_ID, ORG, "review_revision", RESOURCE, "running", 1,
              datetime.now(timezone.utc), None, "worker-a", None)

    def renew(db, **kwargs):
        assert db is heartbeat_db and db is not handler_db
        assert kwargs == dict(org_id=ORG, job_id=JOB_ID, worker_id="worker-a", lease_seconds=.003)
        if outcome == "error":
            raise RuntimeError("database unavailable")
        return outcome

    # Signal after the heartbeat has actually handled its result, without sleeps.
    original_commit, original_rollback = heartbeat_db.commit, heartbeat_db.rollback
    heartbeat_db.commit = lambda: (original_commit(), renewed.set())
    heartbeat_db.rollback = lambda: (original_rollback(), renewed.set())
    monkeypatch.setattr(worker, "renew_lease", renew)
    if outcome == "error":
        monkeypatch.setattr(worker.logger, "warning", lambda *a, **kw: renewed.set())
    context = worker._lease_heartbeat(lambda: heartbeat_db, handler_db, job, "worker-a", .003)
    if outcome is True:
        with context:
            assert renewed.wait(2)
            callbacks[0](handler_db)
        assert heartbeat_db.commits >= 1
    else:
        with pytest.raises(worker.LeaseLost):
            with context as lost:
                assert renewed.wait(2)
                # Renewal false sets lost just after rollback; wait on its actual event.
                assert lost.wait(2)
                with pytest.raises(worker.LeaseLost):
                    callbacks[0](handler_db)
    assert not callbacks
    assert not any(t.name == "job-lease-heartbeat" for t in threading.enumerate())


def test_heartbeat_stops_on_handler_exception(monkeypatch):
    db = FakeSession()
    job = Job(JOB_ID, ORG, "review_revision", RESOURCE, "running", 1,
              datetime.now(timezone.utc), None, "worker-a", None)
    with pytest.raises(ValueError):
        with worker._lease_heartbeat(lambda: FakeSession(), db, job, "worker-a", 300):
            raise ValueError("handler failed")
    assert not any(t.name == "job-lease-heartbeat" for t in threading.enumerate())


@pytest.mark.parametrize("owned", [JOB_ID, None, "db_error"])
def test_publish_commit_locks_unexpired_ownership_using_wall_clock(monkeypatch, owned):
    db = FakeSession()
    callbacks = []
    monkeypatch.setattr(worker.event, "listen", lambda db, name, cb: callbacks.append(cb))
    monkeypatch.setattr(worker.event, "remove", lambda db, name, cb: callbacks.remove(cb))
    job = Job(JOB_ID, ORG, "review_revision", RESOURCE, "running", 1,
              datetime.now(timezone.utc), None, "worker-a", None)

    def execute(statement, params):
        sql = str(statement)
        assert "lease_until > clock_timestamp()" in sql
        assert "FOR UPDATE" in sql and "state = 'running'" in sql
        assert "worker_id = :worker_id" in sql and "org_id = :org_id" in sql
        assert params == dict(job_id=JOB_ID, org_id=ORG, worker_id="worker-a")
        if owned == "db_error":
            raise RuntimeError("sensitive database details")
        return type("Result", (), {"scalar_one_or_none": lambda self: owned})()

    db.execute = execute
    context = worker._lease_heartbeat(lambda: FakeSession(), db, job, "worker-a", 300)
    if owned is None or owned == "db_error":
        with pytest.raises(worker.LeaseLost):
            with context as lost:
                try:
                    callbacks[0](db)
                except worker.LeaseLost:
                    assert lost.is_set()
                    # Handlers may translate commit failures; context still
                    # converts this into abandonment rather than fail_job.
                    raise worker.ReviewFailure("review_result_persistence_error")
    else:
        with context:
            callbacks[0](db)
    assert not callbacks


@pytest.mark.parametrize("handler_fails", [False, True])
def test_run_once_abandons_known_lost_lease_without_terminal_writes(monkeypatch, handler_fails):
    from contextlib import contextmanager

    db = FakeSession()
    job = Job(JOB_ID, ORG, "review_revision", RESOURCE, "running", 1,
              datetime.now(timezone.utc), None, "worker-a", None)
    events = []
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: job)
    monkeypatch.setattr(worker, "fail_exhausted_leases", lambda *a, **kw: [])
    monkeypatch.setattr(worker, "complete_job", lambda *a, **kw: pytest.fail("stale completion"))
    monkeypatch.setattr(worker, "fail_job", lambda *a, **kw: pytest.fail("stale failure"))

    @contextmanager
    def lost_heartbeat(*args):
        original_commit = db.commit

        def reject_commit():
            raise worker.LeaseLost()

        db.commit = reject_commit
        try:
            yield threading.Event()
        finally:
            assert db.rollbacks == 1  # Transaction released before heartbeat join.
            db.commit = original_commit
            events.append("stopped")
            raise worker.LeaseLost()

    def handler(*args):
        events.append("handler")
        if handler_fails:
            raise worker.ReviewFailure("review_validation_error")

    monkeypatch.setattr(worker, "_lease_heartbeat", lost_heartbeat)
    assert worker.run_once(lambda: db, worker_id="worker-a", handlers={"review_revision": handler})
    assert events == ["handler", "stopped"]
    assert db.commits == 2 and db.rollbacks == 2
    assert db.sql == []
