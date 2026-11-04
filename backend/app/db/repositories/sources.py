"""Tenant scoped SQL for newly uploaded source records."""

from datetime import date
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_idempotency_record(db: Session, *, user_id: UUID, endpoint: str, key: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT org_id, request_sha256, response_json, state, expires_at <= now() AS expired
        FROM idempotency_records
        WHERE user_id = :user_id AND endpoint = :endpoint AND key = :key
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


def list_sources(
    db: Session, *, org_id: UUID, status: str | None, after: UUID | None, limit: int
) -> list[dict[str, Any]]:
    """List sources with version state evaluated in the organisation's time zone."""
    rows = db.execute(text("""
        SELECT s.id AS source_id, s.title, s.category, s.archived_at,
               CASE
                 WHEN s.archived_at IS NOT NULL THEN 'archived'
                 WHEN active.id IS NOT NULL THEN 'active'
                 WHEN latest.approval_status = 'pending' THEN 'pending'
                 WHEN latest_approved.id IS NOT NULL THEN 'expired'
                 ELSE 'pending'
               END AS current_status,
               active.id AS active_version_id, active.version_label AS active_version_label,
               latest.id AS latest_version_id, latest.version_label AS latest_version_label,
               latest.ingestion_status, latest.approval_status, latest.ingestion_error_code
        FROM sources AS s
        JOIN organisations AS o ON o.id = s.org_id
        LEFT JOIN LATERAL (
            SELECT v.id, v.version_label
            FROM source_versions AS v
            WHERE v.org_id = s.org_id AND v.source_id = s.id
              AND v.approval_status = 'approved'
              AND s.archived_at IS NULL
              AND v.effective_from <= (now() AT TIME ZONE o.time_zone)::date
              AND (v.effective_to IS NULL OR v.effective_to > (now() AT TIME ZONE o.time_zone)::date)
            ORDER BY v.created_at DESC, v.id DESC
            LIMIT 1
        ) AS active ON true
        LEFT JOIN LATERAL (
            SELECT v.id
            FROM source_versions AS v
            WHERE v.org_id = s.org_id AND v.source_id = s.id
              AND v.approval_status = 'approved'
              AND (v.effective_from > (now() AT TIME ZONE o.time_zone)::date
                   OR (v.effective_to IS NOT NULL AND v.effective_to <= (now() AT TIME ZONE o.time_zone)::date))
            ORDER BY v.created_at DESC, v.id DESC
            LIMIT 1
        ) AS latest_approved ON true
        LEFT JOIN LATERAL (
            SELECT v.id, v.version_label, v.ingestion_status, v.approval_status,
                   v.ingestion_error_code
            FROM source_versions AS v
            WHERE v.org_id = s.org_id AND v.source_id = s.id
            ORDER BY v.created_at DESC, v.id DESC
            LIMIT 1
        ) AS latest ON true
        WHERE s.org_id = :org_id
          AND (CAST(:after AS uuid) IS NULL OR s.id > CAST(:after AS uuid))
          AND (
              CAST(:status AS text) IS NULL
              OR (CAST(:status AS text) = 'active' AND active.id IS NOT NULL)
              OR (CAST(:status AS text) = 'archived' AND s.archived_at IS NOT NULL)
              OR (CAST(:status AS text) = 'expired' AND s.archived_at IS NULL AND active.id IS NULL AND latest_approved.id IS NOT NULL)
              OR (CAST(:status AS text) = 'pending' AND s.archived_at IS NULL AND active.id IS NULL
                  AND (latest.id IS NULL OR latest.approval_status = 'pending'))
              OR (CAST(:status AS text) IN ('queued','processing','ready','failed')
                  AND latest.ingestion_status = CAST(:status AS text))
          )
        ORDER BY s.id
        LIMIT :limit
    """), {"org_id": org_id, "status": status, "after": after, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def get_source_version(
    db: Session, *, org_id: UUID, source_id: UUID, version_id: UUID
) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT s.id AS source_id, v.id AS version_id, s.title, s.category,
               v.version_label, v.effective_from, v.effective_to,
               v.ingestion_status, v.approval_status, v.ingestion_error_code,
               v.r2_object_key IS NOT NULL AS original_available,
               (SELECT count(*) FROM source_passages AS p
                WHERE p.org_id = v.org_id AND p.source_id = v.source_id
                  AND p.version_id = v.id) AS passage_count
        FROM sources AS s
        JOIN source_versions AS v
          ON v.org_id = s.org_id AND v.source_id = s.id
        WHERE s.org_id = :org_id AND s.id = :source_id AND v.id = :version_id
    """), {"org_id": org_id, "source_id": source_id, "version_id": version_id}).mappings().first()
    return dict(row) if row else None


def get_source_original(
    db: Session, *, org_id: UUID, source_id: UUID, version_id: UUID
) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT v.r2_object_key, v.mime_type
        FROM sources AS s
        JOIN source_versions AS v
          ON v.org_id = s.org_id AND v.source_id = s.id
        WHERE s.org_id = :org_id AND s.id = :source_id AND v.id = :version_id
    """), {"org_id": org_id, "source_id": source_id, "version_id": version_id}).mappings().first()
    return dict(row) if row else None


def lock_org_for_approval(db: Session, *, org_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT id, corpus_version, time_zone
        FROM organisations WHERE id = :org_id FOR UPDATE
    """), {"org_id": org_id}).mappings().first()
    return dict(row) if row else None


def lock_source_for_approval(db: Session, *, org_id: UUID, source_id: UUID) -> bool:
    row = db.execute(text("""
        SELECT id FROM sources
        WHERE org_id = :org_id AND id = :source_id FOR UPDATE
    """), {"org_id": org_id, "source_id": source_id}).first()
    return row is not None


def lock_version_for_approval(
    db: Session, *, org_id: UUID, source_id: UUID, version_id: UUID
) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT id, effective_from, effective_to, ingestion_status, approval_status
        FROM source_versions
        WHERE org_id = :org_id AND source_id = :source_id AND id = :version_id
        FOR UPDATE
    """), {"org_id": org_id, "source_id": source_id, "version_id": version_id}).mappings().first()
    return dict(row) if row else None


def version_has_passages(db: Session, *, org_id: UUID, source_id: UUID, version_id: UUID) -> bool:
    return db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM source_passages
                       WHERE org_id = :org_id AND source_id = :source_id AND version_id = :version_id)
    """), {"org_id": org_id, "source_id": source_id, "version_id": version_id}).scalar_one()


def activate_source_version(
    db: Session, *, org_id: UUID, source_id: UUID, version_id: UUID, corpus_version: int
) -> None:
    db.execute(text("""
        UPDATE source_versions SET approval_status = 'archived', archived_at = now()
        WHERE org_id = :org_id AND source_id = :source_id AND approval_status = 'approved'
    """), {"org_id": org_id, "source_id": source_id})
    db.execute(text("""
        UPDATE source_versions SET approval_status = 'approved', approved_at = now(), archived_at = NULL
        WHERE org_id = :org_id AND source_id = :source_id AND id = :version_id
    """), {"org_id": org_id, "source_id": source_id, "version_id": version_id})
    db.execute(text("""
        UPDATE sources SET archived_at = NULL WHERE org_id = :org_id AND id = :source_id
    """), {"org_id": org_id, "source_id": source_id})
    db.execute(text("""
        UPDATE organisations SET corpus_version = :corpus_version, updated_at = now()
        WHERE id = :org_id
    """), {"corpus_version": corpus_version, "org_id": org_id})
