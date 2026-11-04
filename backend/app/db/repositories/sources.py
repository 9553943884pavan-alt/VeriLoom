"""Tenant scoped SQL for newly uploaded source records."""

from datetime import date
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_idempotency_record(db: Session, *, user_id: UUID, endpoint: str, key: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT org_id, request_sha256, response_json, state
        FROM idempotency_records
        WHERE user_id = :user_id AND endpoint = :endpoint AND key = :key
          AND expires_at > now()
    """), {"user_id": user_id, "endpoint": endpoint, "key": key}).mappings().first()
    return dict(row) if row else None


def insert_source_upload(
    db: Session,
    *,
    org_id: UUID,
    source_id: UUID,
    version_id: UUID,
    actor_id: UUID,
    title: str,
    category: str,
    object_key: str,
    sha256: str,
    mime_type: str,
    byte_size: int,
    effective_from: date,
    effective_to: date | None,
) -> None:
    """Insert the source and its immutable first version in caller transaction."""
    db.execute(text("""
        INSERT INTO sources (id, org_id, title, category, created_by_user_id)
        VALUES (:source_id, :org_id, :title, :category, :actor_id)
    """), {"source_id": source_id, "org_id": org_id, "title": title,
           "category": category, "actor_id": actor_id})
    db.execute(text("""
        INSERT INTO source_versions (
            id, org_id, source_id, version_label, r2_object_key, sha256,
            mime_type, byte_size, effective_from, effective_to,
            ingestion_status, approval_status, uploaded_by_user_id
        ) VALUES (
            :version_id, :org_id, :source_id, '1.0', :object_key, :sha256,
            :mime_type, :byte_size, :effective_from, :effective_to,
            'queued', 'pending', :actor_id
        )
    """), {"version_id": version_id, "org_id": org_id, "source_id": source_id,
           "object_key": object_key, "sha256": sha256, "mime_type": mime_type,
           "byte_size": byte_size, "effective_from": effective_from,
           "effective_to": effective_to, "actor_id": actor_id})


def insert_idempotency_record(
    db: Session,
    *,
    user_id: UUID,
    org_id: UUID,
    key: UUID,
    request_sha256: str,
    source_id: UUID,
    response_json: dict[str, Any],
) -> None:
    """Persist a completed upload replay record in the source transaction."""
    db.execute(text("""
        INSERT INTO idempotency_records (
            user_id, org_id, endpoint, key, request_sha256, response_status,
            response_json, resource_id, state, expires_at
        ) VALUES (
            :user_id, :org_id, '/organisations/{org_id}/sources', :key,
            :request_sha256, 202, CAST(:response_json AS jsonb), :source_id,
            'complete', now() + interval '24 hours'
        )
    """), {"user_id": user_id, "org_id": org_id, "key": key,
           "request_sha256": request_sha256, "response_json": json.dumps(response_json),
           "source_id": source_id})
