"""Short, tenant-scoped operations for the PostgreSQL job queue.

These functions participate in the caller's SQLAlchemy transaction. In
particular, a worker must commit immediately after ``claim_job`` and before
calling a handler or external service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Collection, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

JobKind = Literal["ingest_source", "review_revision", "answer_question"]
JobState = Literal["queued", "running", "succeeded", "failed"]
_JOB_KINDS = {"ingest_source", "review_revision", "answer_question"}


@dataclass(frozen=True)
class Job:
    id: UUID
    org_id: UUID
    kind: JobKind
    resource_id: UUID
    state: JobState
    attempts: int
    run_after: datetime
    lease_until: datetime | None
    worker_id: str | None
    last_error_code: str | None


def _job(row: Any) -> Job | None:
    return Job(**dict(row)) if row is not None else None


def _validate_kind(kind: str) -> None:
    if kind not in _JOB_KINDS:
        raise ValueError("Unsupported job kind")


def enqueue_job(db: Session, *, org_id: UUID, kind: JobKind, resource_id: UUID) -> UUID:
    """Insert one durable job in the caller's resource transaction."""
    _validate_kind(kind)
    return db.execute(text("""
        INSERT INTO jobs (org_id, kind, resource_id)
        VALUES (:org_id, :kind, :resource_id)
        RETURNING id
    """), {"org_id": org_id, "kind": kind, "resource_id": resource_id}).scalar_one()


def claim_job(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    max_attempts: int = 5,
    allowed_kinds: Collection[JobKind] | None = None,
) -> Job | None:
    """Claim a due job or reclaim an expired lease without blocking peers."""
    if not worker_id or len(worker_id) > 200:
        raise ValueError("worker_id must contain 1 to 200 characters")
    if lease_seconds < 1 or max_attempts < 1:
        raise ValueError("lease_seconds and max_attempts must be positive")
    if allowed_kinds is not None:
        allowed_kinds = tuple(sorted(set(allowed_kinds)))
        if not allowed_kinds or any(kind not in _JOB_KINDS for kind in allowed_kinds):
            raise ValueError("allowed_kinds must contain supported job kinds")
    candidate_kind_filter = " AND kind = ANY(:allowed_kinds)" if allowed_kinds is not None else ""
    params = {"worker_id": worker_id, "lease_seconds": lease_seconds, "max_attempts": max_attempts}
    if allowed_kinds is not None:
        params["allowed_kinds"] = list(allowed_kinds)
    row = db.execute(text("""
        WITH candidate AS (
            SELECT id
            FROM jobs
            WHERE attempts < :max_attempts
              {candidate_kind_filter}
              AND ((state = 'queued' AND run_after <= now())
                   OR (state = 'running' AND lease_until <= now()))
            ORDER BY run_after, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE jobs AS j
        SET state = 'running', attempts = j.attempts + 1,
            lease_until = now() + make_interval(secs => :lease_seconds),
            worker_id = :worker_id, updated_at = now()
        FROM candidate
        WHERE j.id = candidate.id
        RETURNING j.id, j.org_id, j.kind, j.resource_id, j.state, j.attempts,
                  j.run_after, j.lease_until, j.worker_id, j.last_error_code
    """.format(candidate_kind_filter=candidate_kind_filter)), params).mappings().first()
    return _job(row)


def fail_exhausted_leases(
    db: Session, *, allowed_kinds: Collection[JobKind], max_attempts: int = 5, limit: int = 20,
) -> list[Job]:
    """Fail expired final attempts so the worker can fail their resources too."""
    kinds = tuple(sorted(set(allowed_kinds)))
    if not kinds or any(kind not in _JOB_KINDS for kind in kinds):
        raise ValueError("allowed_kinds must contain supported job kinds")
    if max_attempts < 1 or not 1 <= limit <= 100:
        raise ValueError("invalid exhausted-lease sweep limits")
    rows = db.execute(text("""
        WITH candidates AS (
            SELECT id FROM jobs
            WHERE kind = ANY(:allowed_kinds) AND state = 'running'
              AND lease_until <= now() AND attempts >= :max_attempts
            ORDER BY lease_until, id
            FOR UPDATE SKIP LOCKED
            LIMIT :limit
        )
        UPDATE jobs AS j SET state = 'failed', worker_id = NULL, lease_until = NULL,
            last_error_code = 'attempts_exhausted', updated_at = now()
        FROM candidates WHERE j.id = candidates.id
        RETURNING j.id, j.org_id, j.kind, j.resource_id, j.state, j.attempts,
                  j.run_after, j.lease_until, j.worker_id, j.last_error_code
    """), {"allowed_kinds": list(kinds), "max_attempts": max_attempts, "limit": limit}).mappings().all()
    return [_job(row) for row in rows]


def complete_job(db: Session, *, org_id: UUID, job_id: UUID, worker_id: str) -> bool:
    """Mark a still leased job successful; stale workers cannot complete it."""
    result = db.execute(text("""
        UPDATE jobs SET state = 'succeeded', lease_until = NULL, worker_id = NULL,
            last_error_code = NULL, updated_at = now()
        WHERE id = :job_id AND org_id = :org_id AND state = 'running'
          AND worker_id = :worker_id AND lease_until > now()
    """), {"job_id": job_id, "org_id": org_id, "worker_id": worker_id})
    return result.rowcount == 1


def renew_lease(db: Session, *, org_id: UUID, job_id: UUID, worker_id: str, lease_seconds: int = 300) -> bool:
    """Extend only the current worker's unexpired lease."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    result = db.execute(text("""
        UPDATE jobs SET lease_until = now() + make_interval(secs => :lease_seconds), updated_at = now()
        WHERE id = :job_id AND org_id = :org_id AND state = 'running'
          AND worker_id = :worker_id AND lease_until > now()
    """), {"job_id": job_id, "org_id": org_id, "worker_id": worker_id,
           "lease_seconds": lease_seconds})
    return result.rowcount == 1


def fail_job(
    db: Session,
    *,
    org_id: UUID,
    job_id: UUID,
    worker_id: str,
    error_code: str,
    retryable: bool = True,
    max_attempts: int = 5,
    base_delay_seconds: int = 5,
    max_delay_seconds: int = 900,
) -> JobState | None:
    """Store a safe error code and retry with bounded exponential backoff."""
    if not error_code or len(error_code) > 100:
        raise ValueError("error_code must contain 1 to 100 characters")
    if max_attempts < 1 or base_delay_seconds < 1 or max_delay_seconds < 1:
        raise ValueError("attempt and backoff limits must be positive")
    row = db.execute(text("""
        UPDATE jobs
        SET state = CASE WHEN :retryable AND attempts < :max_attempts THEN 'queued' ELSE 'failed' END,
            run_after = CASE WHEN :retryable AND attempts < :max_attempts
                             THEN now() + make_interval(secs => LEAST(
                                 :max_delay_seconds,
                                 :base_delay_seconds * power(2, LEAST(attempts - 1, 30))
                             )::integer) ELSE run_after END,
            lease_until = NULL, worker_id = NULL, last_error_code = :error_code, updated_at = now()
        WHERE id = :job_id AND org_id = :org_id AND state = 'running'
          AND worker_id = :worker_id AND lease_until > now()
        RETURNING state
    """), {"job_id": job_id, "org_id": org_id, "worker_id": worker_id,
           "error_code": error_code, "retryable": retryable, "max_attempts": max_attempts,
           "base_delay_seconds": base_delay_seconds, "max_delay_seconds": max_delay_seconds}).scalar_one_or_none()
    return row


def requeue_job(
    db: Session,
    *,
    org_id: UUID,
    job_id: UUID,
    delay_seconds: int = 0,
    max_attempts: int = 5,
) -> bool:
    """Explicitly requeue an existing job, subject to its attempt budget."""
    if delay_seconds < 0 or max_attempts < 1:
        raise ValueError("delay_seconds must be nonnegative and max_attempts positive")
    result = db.execute(text("""
        UPDATE jobs SET state = 'queued', run_after = now() + make_interval(secs => :delay_seconds),
            lease_until = NULL, worker_id = NULL, updated_at = now()
        WHERE id = :job_id AND org_id = :org_id AND state = 'failed'
          AND attempts < :max_attempts
    """), {"job_id": job_id, "org_id": org_id, "delay_seconds": delay_seconds,
           "max_attempts": max_attempts})
    return result.rowcount == 1
