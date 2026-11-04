"""Queue SQL contracts and guards, tested without a PostgreSQL instance."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.jobs import queue

ORG = UUID("11111111-1111-4111-8111-111111111111")
JOB = UUID("22222222-2222-4222-8222-222222222222")
RESOURCE = UUID("33333333-3333-4333-8333-333333333333")


class FakeResult:
    def __init__(self, *, row=None, scalar=None, rowcount=1, rows=None):
        self.row = row
        self.scalar = scalar
        self.rowcount = rowcount
        self.rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


class FakeSession:
    def __init__(self, result=None):
        self.result = result or FakeResult()
        self.sql = ""
        self.params = {}

    def execute(self, statement, params):
        self.sql = str(statement)
        self.params = params
        return self.result


def test_claim_uses_skip_locked_and_reclaims_expired_leases() -> None:
    now = datetime.now(timezone.utc)
    db = FakeSession(FakeResult(row={
        "id": JOB, "org_id": ORG, "kind": "review_revision", "resource_id": RESOURCE,
        "state": "running", "attempts": 2, "run_after": now, "lease_until": now,
        "worker_id": "worker-a", "last_error_code": None,
    }))

    claimed = queue.claim_job(db, worker_id="worker-a", lease_seconds=30, max_attempts=4)

    assert claimed is not None
    assert claimed.id == JOB and claimed.org_id == ORG and claimed.resource_id == RESOURCE
    assert claimed.attempts == 2
    assert "FOR UPDATE SKIP LOCKED" in db.sql
    assert "lease_until <= now()" in db.sql
    assert "attempts = j.attempts + 1" in db.sql
    assert db.params == {"worker_id": "worker-a", "lease_seconds": 30, "max_attempts": 4}


def test_claim_validates_worker_and_limits_without_querying() -> None:
    db = FakeSession()
    with pytest.raises(ValueError):
        queue.claim_job(db, worker_id="", lease_seconds=30)
    with pytest.raises(ValueError):
        queue.claim_job(db, worker_id="w", lease_seconds=0)
    with pytest.raises(ValueError):
        queue.claim_job(db, worker_id="w", allowed_kinds=[])
    assert db.sql == ""


def test_claim_can_limit_job_kinds_for_a_partial_worker() -> None:
    db = FakeSession(FakeResult(row=None))
    assert queue.claim_job(db, worker_id="worker-a", allowed_kinds=("ingest_source",)) is None
    assert db.sql.count("AND kind = ANY(:allowed_kinds)") == 1
    assert "j.kind = ANY(:allowed_kinds)" not in db.sql
    assert db.params["allowed_kinds"] == ["ingest_source"]


def test_exhausted_lease_sweep_returns_resources_for_terminal_failure() -> None:
    now = datetime.now(timezone.utc)
    db = FakeSession(FakeResult(rows=[{
        "id": JOB, "org_id": ORG, "kind": "review_revision", "resource_id": RESOURCE,
        "state": "failed", "attempts": 5, "run_after": now, "lease_until": None,
        "worker_id": None, "last_error_code": "attempts_exhausted",
    }]))
    expired = queue.fail_exhausted_leases(
        db, allowed_kinds=("review_revision",), max_attempts=5,
    )
    assert len(expired) == 1 and expired[0].resource_id == RESOURCE
    assert "FOR UPDATE SKIP LOCKED" in db.sql
    assert "kind = ANY(:allowed_kinds)" in db.sql
    assert db.params["allowed_kinds"] == ["review_revision"]


def test_enqueue_validates_kind_and_binds_tenant_resource_tuple() -> None:
    db = FakeSession(FakeResult(scalar=JOB))
    result = queue.enqueue_job(db, org_id=ORG, kind="answer_question", resource_id=RESOURCE)
    assert result == JOB
    assert "INSERT INTO jobs (org_id, kind, resource_id)" in db.sql
    assert db.params == {"org_id": ORG, "kind": "answer_question", "resource_id": RESOURCE}

    with pytest.raises(ValueError):
        queue.enqueue_job(db, org_id=ORG, kind="unknown", resource_id=RESOURCE)


def test_completion_is_scoped_to_org_worker_and_live_lease() -> None:
    db = FakeSession(FakeResult(rowcount=0))
    assert not queue.complete_job(db, org_id=ORG, job_id=JOB, worker_id="worker-a")
    assert "org_id = :org_id" in db.sql
    assert "worker_id = :worker_id" in db.sql
    assert "lease_until > now()" in db.sql
    assert db.params == {"job_id": JOB, "org_id": ORG, "worker_id": "worker-a"}


def test_lease_renewal_is_scoped_to_current_worker() -> None:
    db = FakeSession(FakeResult(rowcount=1))
    assert queue.renew_lease(db, org_id=ORG, job_id=JOB, worker_id="worker-a", lease_seconds=60)
    assert "worker_id = :worker_id" in db.sql
    assert "lease_until > now()" in db.sql
    assert db.params["org_id"] == ORG
    assert db.params["lease_seconds"] == 60


def test_fail_retries_only_until_attempt_budget_with_safe_backoff_parameters() -> None:
    db = FakeSession(FakeResult(scalar="queued"))
    result = queue.fail_job(db, org_id=ORG, job_id=JOB, worker_id="worker-a", error_code="provider_timeout",
                            max_attempts=4, base_delay_seconds=3, max_delay_seconds=20)
    assert result == "queued"
    assert "attempts < :max_attempts" in db.sql
    assert "power(2, LEAST(attempts - 1, 30))" in db.sql
    assert db.params["base_delay_seconds"] == 3
    assert db.params["max_delay_seconds"] == 20
    assert db.params["error_code"] == "provider_timeout"


def test_requeue_requires_same_org_and_attempt_budget() -> None:
    db = FakeSession(FakeResult(rowcount=1))
    assert queue.requeue_job(db, org_id=ORG, job_id=JOB, delay_seconds=15, max_attempts=4)
    assert "org_id = :org_id" in db.sql
    assert "attempts < :max_attempts" in db.sql
    assert db.params == {"job_id": JOB, "org_id": ORG, "delay_seconds": 15, "max_attempts": 4}
