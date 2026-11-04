"""Source upload workflow spanning private object storage and PostgreSQL."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.repositories import sources as repo
from app.integrations.r2 import R2ConfigurationError, R2Storage, R2StorageError
from app.jobs.queue import enqueue_job
from app.rag.ingestion import (
    GuidanceTooLarge,
    InvalidGuidanceText,
    UnsupportedGuidanceFormat,
    extract_guidance,
)
from app.schemas.sources import SourceApprovalRequest, SourceUploadAccepted, SourceUploadMetadata
from app.services.organisations import _require_role

IDEMPOTENCY_ENDPOINT = "/organisations/{org_id}/sources"


def approve_source_version(
    db: Session,
    *,
    org_id: UUID,
    source_id: UUID,
    version_id: UUID,
    actor_id: UUID,
    request: SourceApprovalRequest,
) -> dict:
    """Atomically activate an ingested, currently effective guidance version."""
    org = repo.lock_org_for_approval(db, org_id=org_id)
    if org is None:
        raise ApiError(404, "not_found", "The source was not found.")
    # Membership updates also lock the organisation first, keeping this check
    # stable until the activation transaction commits.
    _require_role(db, org_id, actor_id, {"admin"})
    if org["corpus_version"] != request.expected_corpus_version:
        raise ApiError(409, "stale_version", "The guidance corpus changed. Refresh before approving.",
                       {"current_corpus_version": org["corpus_version"]})
    if not repo.lock_source_for_approval(db, org_id=org_id, source_id=source_id):
        raise ApiError(404, "not_found", "The source was not found.")
    version = repo.lock_version_for_approval(
        db, org_id=org_id, source_id=source_id, version_id=version_id
    )
    if version is None:
        raise ApiError(404, "not_found", "The source version was not found.")
    if version["ingestion_status"] != "ready" or version["approval_status"] != "pending":
        raise ApiError(409, "source_not_approvable", "Only a ready, pending source version can be approved.")
    try:
        today = datetime.now(ZoneInfo(org["time_zone"])).date()
    except (ZoneInfoNotFoundError, TypeError) as exc:
        raise ApiError(500, "invalid_organisation_timezone", "The organisation time zone is invalid.") from exc
    if version["effective_from"] > today:
        raise ApiError(409, "source_not_effective", "The source version is not effective yet.")
    if version["effective_to"] is not None and version["effective_to"] <= today:
        raise ApiError(409, "source_expired", "The source version has expired.")
    if not repo.version_has_passages(db, org_id=org_id, source_id=source_id, version_id=version_id):
        raise ApiError(409, "source_has_no_passages", "A source version needs passages before approval.")
    corpus_version = org["corpus_version"] + 1
    repo.activate_source_version(
        db, org_id=org_id, source_id=source_id, version_id=version_id, corpus_version=corpus_version
    )
    db.commit()
    return {"source_id": source_id, "approved_version_id": version_id, "corpus_version": corpus_version}


def _request_digest(content: bytes, metadata: SourceUploadMetadata, mime_type: str) -> str:
    return hashlib.sha256(
        metadata.model_dump_json().encode("utf-8") + b"\0" + mime_type.encode("ascii") + b"\0" + content
    ).hexdigest()


def upload_source(
    db: Session,
    storage: R2Storage | Callable[[], R2Storage],
    *,
    org_id: UUID,
    actor_id: UUID,
    metadata: SourceUploadMetadata,
    filename: str,
    content: bytes,
    idempotency_key: UUID | None = None,
) -> dict:
    """Validate and queue a private guidance upload atomically with its job."""
    _require_role(db, org_id, actor_id, {"admin"})
    try:
        extracted = extract_guidance(filename, content)
    except GuidanceTooLarge as exc:
        raise ApiError(413, "file_too_large", "The file exceeds the 2 MiB limit.") from exc
    except UnsupportedGuidanceFormat as exc:
        raise ApiError(415, "unsupported_file", "Upload a valid UTF-8 .txt or .md file.") from exc
    except InvalidGuidanceText as exc:
        raise ApiError(422, "empty_file", "The file must contain text.") from exc

    digest = _request_digest(content, metadata, extracted.mime_type) if idempotency_key is not None else None
    if digest is not None:
        digest = hashlib.sha256(str(org_id).encode("ascii") + b"\0" + digest.encode("ascii")).hexdigest()
    if idempotency_key is not None:
        existing = repo.get_idempotency_record(
            db, user_id=actor_id, endpoint=IDEMPOTENCY_ENDPOINT, key=idempotency_key
        )
        if existing is not None:
            if existing.get("expired", False):
                raise ApiError(409, "idempotency_key_expired", "This key has expired and cannot be reused.")
            if existing["org_id"] != org_id or existing["request_sha256"] != digest:
                raise ApiError(409, "idempotency_key_reused", "This key was used for a different request.")
            if existing["state"] == "complete":
                return SourceUploadAccepted.model_validate(existing["response_json"]).model_dump()
            raise ApiError(409, "request_in_progress", "This upload is already being processed.")

    if callable(storage):
        try:
            storage = storage()
        except R2ConfigurationError as exc:
            raise ApiError(503, "storage_unavailable", "Original file storage is not configured.") from exc

    source_id, version_id = uuid4(), uuid4()
    object_key = f"org/{org_id}/sources/{source_id}/versions/{version_id}/original"
    result = {"source_id": source_id, "version_id": version_id, "ingestion_status": "queued"}
    try:
        storage.put_bytes(object_key, content, extracted.mime_type)
    except R2StorageError as exc:
        raise ApiError(503, "storage_unavailable", "The original file could not be stored.") from exc

    try:
        repo.insert_source_upload(
            db, org_id=org_id, source_id=source_id, version_id=version_id,
            actor_id=actor_id, title=metadata.title, category=metadata.category,
            object_key=object_key, sha256=extracted.sha256,
            mime_type=extracted.mime_type, byte_size=extracted.byte_size,
            effective_from=metadata.effective_from, effective_to=metadata.effective_to,
        )
        enqueue_job(db, org_id=org_id, kind="ingest_source", resource_id=version_id)
        if idempotency_key is not None:
            repo.insert_idempotency_record(
                db, user_id=actor_id, org_id=org_id, key=idempotency_key,
                request_sha256=digest, source_id=source_id,
                response_json={**result, "source_id": str(source_id), "version_id": str(version_id)},
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(object_key)
        except R2StorageError:
            # The orphan remains private and can be removed by storage reconciliation.
            pass
        if isinstance(exc, IntegrityError):
            if idempotency_key is not None:
                existing = repo.get_idempotency_record(
                    db, user_id=actor_id, endpoint=IDEMPOTENCY_ENDPOINT, key=idempotency_key
                )
                if existing is not None:
                    if (not existing.get("expired", False) and existing["org_id"] == org_id
                            and existing["request_sha256"] == digest and existing["state"] == "complete"):
                        return SourceUploadAccepted.model_validate(existing["response_json"]).model_dump()
                    if existing.get("expired", False):
                        raise ApiError(409, "idempotency_key_expired", "This key has expired and cannot be reused.") from exc
                    raise ApiError(409, "idempotency_key_reused", "This key was used for a different request.") from exc
        raise
    return result
