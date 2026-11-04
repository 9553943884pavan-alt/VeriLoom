from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.jobs import worker
from app.jobs.queue import Job

ORG = UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = UUID("22222222-2222-4222-8222-222222222222")
RESOURCE = UUID("33333333-3333-4333-8333-333333333333")


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
