"""Polling loop for the durable PostgreSQL job queue."""

from __future__ import annotations

import logging
import os
import socket
import threading
from sqlalchemy import text
from collections.abc import Callable, Mapping
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.jobs.handlers.ingest_source import IngestionFailure, build_ingestion_dependencies, ingest_source
from app.jobs.handlers.answer_question import AnswerFailure, answer_question, build_answer_dependencies
from app.jobs.handlers.review_revision import ReviewFailure, build_review_dependencies, review_revision
from app.jobs.queue import Job, claim_job, complete_job, fail_exhausted_leases, fail_job

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
JobHandler = Callable[[Session, Job], None]


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"


def _ingest_handler(db: Session, job: Job) -> None:
    storage, embedder = build_ingestion_dependencies()
    ingest_source(db, org_id=job.org_id, version_id=job.resource_id, storage=storage, embedder=embedder)


def _review_handler(db: Session, job: Job) -> None:
    embedder, chat = build_review_dependencies()
    try:
        review_revision(db, org_id=job.org_id, review_id=job.resource_id, embedder=embedder, chat=chat)
    finally:
        chat.close()


def _answer_handler(db: Session, job: Job) -> None:
    embedder, chat = build_answer_dependencies()
    try:
        answer_question(db, org_id=job.org_id, turn_id=job.resource_id, embedder=embedder, chat=chat)
    finally:
        chat.close()


def _mark_resource_failed(db: Session, job: Job, code: str) -> None:
    if job.kind == "ingest_source":
        db.execute(text("""
            UPDATE source_versions SET ingestion_status = 'failed', ingestion_error_code = :code
            WHERE org_id = :org_id AND id = :resource_id AND ingestion_status <> 'ready'
        """), {"code": code, "org_id": job.org_id, "resource_id": job.resource_id})
    elif job.kind == "review_revision":
        db.execute(text("""
            UPDATE reviews SET state = 'failed', error_code = :code, updated_at = now()
            WHERE org_id = :org_id AND id = :resource_id AND state IN ('queued','processing')
        """), {"code": code, "org_id": job.org_id, "resource_id": job.resource_id})
        db.execute(text("""
            UPDATE drafts AS d SET state = 'review_failed', updated_at = now()
            FROM draft_revisions AS v JOIN reviews AS r
              ON r.org_id = v.org_id AND r.revision_id = v.id
            WHERE r.org_id = :org_id AND r.id = :resource_id
              AND d.org_id = v.org_id AND d.id = v.draft_id
              AND d.current_revision_id = v.id AND d.state = 'processing'
        """), {"org_id": job.org_id, "resource_id": job.resource_id})
    elif job.kind == "answer_question":
        db.execute(text("""
            UPDATE chat_turns SET state='failed', error_code=:code
            WHERE org_id=:org_id AND id=:resource_id AND state IN ('queued','processing')
        """), {"code": code, "org_id": job.org_id, "resource_id": job.resource_id})


def run_once(
    session_factory: SessionFactory,
    *,
    worker_id: str,
    handlers: Mapping[str, JobHandler] | None = None,
    lease_seconds: int = 300,
    max_attempts: int = 5,
) -> bool:
    """Claim and process one job; return whether a job was available."""
    handlers = {"ingest_source": _ingest_handler, "review_revision": _review_handler,
                "answer_question": _answer_handler} if handlers is None else handlers
    if not handlers:
        return False
    with session_factory() as db:
        exhausted = fail_exhausted_leases(
            db, allowed_kinds=tuple(handlers), max_attempts=max_attempts,
        )
        for expired in exhausted:
            _mark_resource_failed(db, expired, "attempts_exhausted")
        db.commit()
        job = claim_job(
            db, worker_id=worker_id, lease_seconds=lease_seconds,
            max_attempts=max_attempts, allowed_kinds=tuple(handlers),
        )
        db.commit()  # Persist the claim before handler I/O/model work begins.
        if job is None:
            return False
        try:
            handler = handlers[job.kind]
            handler(db, job)
            db.commit()
            if not complete_job(db, org_id=job.org_id, job_id=job.id, worker_id=worker_id):
                db.rollback()
                logger.warning("Job lease expired before completion", extra={"job_id": str(job.id)})
            else:
                db.commit()
        except (IngestionFailure, ReviewFailure, AnswerFailure) as exc:
            db.rollback()
            state = fail_job(
                db, org_id=job.org_id, job_id=job.id, worker_id=worker_id,
                error_code=exc.code, retryable=exc.retryable, max_attempts=max_attempts,
            )
            if state == "failed":
                _mark_resource_failed(db, job, exc.code)
            db.commit()
            logger.warning("Job failed", extra={"job_id": str(job.id), "error_code": exc.code})
        except Exception:
            db.rollback()
            state = fail_job(
                db, org_id=job.org_id, job_id=job.id, worker_id=worker_id,
                error_code="worker_unexpected_error", retryable=True, max_attempts=max_attempts,
            )
            if state == "failed":
                _mark_resource_failed(db, job, "worker_unexpected_error")
            db.commit()
            logger.exception("Job failed unexpectedly", extra={"job_id": str(job.id)})
    return True


def run_forever(
    session_factory: SessionFactory,
    *,
    worker_id: str,
    stop_event: threading.Event,
    poll_seconds: float = 2.0,
    handlers: Mapping[str, JobHandler] | None = None,
) -> None:
    """Poll at a bounded interval until asked to stop."""
    if poll_seconds <= 0 or poll_seconds > 60:
        raise ValueError("poll_seconds must be between 0 and 60")
    while not stop_event.is_set():
        if not run_once(session_factory, worker_id=worker_id, handlers=handlers):
            stop_event.wait(poll_seconds)


def main() -> None:
    """Console entry point: ``python -m app.jobs.worker``."""
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    stop = threading.Event()
    try:
        run_forever(lambda: Session(bind=get_engine()), worker_id=default_worker_id(), stop_event=stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
