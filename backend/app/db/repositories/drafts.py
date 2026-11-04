"""Tenant-scoped draft and revision SQL operations."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def insert_draft(db: Session, *, org_id: UUID, user_id: UUID, title: str, channel: str, content: str) -> dict[str, Any]:
    row = db.execute(text("""
        INSERT INTO drafts (org_id, creator_user_id, title, channel, working_content)
        VALUES (:org_id, :user_id, :title, :channel, :content)
        RETURNING id AS draft_id, state, working_copy_version
    """), {"org_id": org_id, "user_id": user_id, "title": title, "channel": channel, "content": content}).mappings().one()
    return dict(row)


def list_drafts(db: Session, *, org_id: UUID, user_id: UUID, view: str, state: str | None, after: UUID | None, limit: int) -> list[dict[str, Any]]:
    if view == "mine":
        where = "d.creator_user_id = :user_id"
    else:
        where = "EXISTS (SELECT 1 FROM reviews r WHERE r.org_id=d.org_id AND r.revision_id=d.current_revision_id AND r.reviewer_user_id=:user_id)"
    rows = db.execute(text(f"""
        SELECT d.id AS draft_id, d.title, d.channel, d.state, d.working_copy_version,
               d.updated_at, d.creator_user_id, r.reviewer_user_id AS assigned_reviewer_user_id,
               r.state AS review_state
        FROM drafts d LEFT JOIN reviews r ON r.org_id=d.org_id AND r.revision_id=d.current_revision_id
        WHERE d.org_id=:org_id AND {where} AND (:state IS NULL OR d.state=:state)
          AND (:after IS NULL OR d.id > :after)
        ORDER BY d.id LIMIT :limit
    """), {"org_id": org_id, "user_id": user_id, "state": state, "after": after, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def get_draft(db: Session, *, org_id: UUID, draft_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT d.id AS draft_id, d.creator_user_id, d.title, d.channel, d.content_format,
               d.working_content, d.working_copy_version, d.state, d.current_revision_id,
               d.created_at, d.updated_at, r.id AS review_id, r.reviewer_user_id AS assigned_reviewer_user_id,
               r.state AS review_state, r.review_version
        FROM drafts d LEFT JOIN reviews r ON r.org_id=d.org_id AND r.revision_id=d.current_revision_id
        WHERE d.org_id=:org_id AND d.id=:draft_id
    """), {"org_id": org_id, "draft_id": draft_id}).mappings().first()
    return dict(row) if row else None


def update_draft(db: Session, *, org_id: UUID, draft_id: UUID, user_id: UUID, expected: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    assignments = [f"{name} = :{name}" for name in fields]
    assignments += ["working_copy_version=working_copy_version+1", "updated_at=now()"]
    params = {"org_id": org_id, "draft_id": draft_id, "user_id": user_id, "expected": expected, **fields}
    row = db.execute(text(f"""
        UPDATE drafts SET {', '.join(assignments)}
        WHERE org_id=:org_id AND id=:draft_id AND creator_user_id=:user_id
          AND working_copy_version=:expected AND state IN ('editing','changes_requested')
        RETURNING id AS draft_id, title, channel, working_content AS content, state, working_copy_version
    """), params).mappings().first()
    return dict(row) if row else None


def lock_draft_for_submit(db: Session, *, org_id: UUID, draft_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""SELECT id, creator_user_id, title, channel, working_content,
        working_copy_version, state FROM drafts WHERE org_id=:org_id AND id=:draft_id FOR UPDATE"""),
        {"org_id": org_id, "draft_id": draft_id}).mappings().first()
    return dict(row) if row else None


def reviewer_is_active(db: Session, *, org_id: UUID, user_id: UUID) -> bool:
    return bool(db.execute(text("""SELECT EXISTS(SELECT 1 FROM memberships WHERE org_id=:org_id AND user_id=:user_id AND status='active' AND 'reviewer'=ANY(roles))"""), {"org_id": org_id, "user_id": user_id}).scalar_one())


def create_submission(db: Session, *, org_id: UUID, draft: dict[str, Any], reviewer_id: UUID, content: str, digest: str) -> dict[str, Any]:
    revision = db.execute(text("""
        INSERT INTO draft_revisions (org_id,draft_id,revision_number,title,channel,content_format,content_nfc,content_sha256,submitted_by_user_id)
        SELECT :org_id,:draft_id,COALESCE(MAX(revision_number),0)+1,:title,:channel,'text',:content,:digest,:creator_id
        FROM draft_revisions WHERE org_id=:org_id AND draft_id=:draft_id
        RETURNING id, revision_number
    """), {"org_id": org_id, "draft_id": draft["id"], "title": draft["title"], "channel": draft["channel"], "content": content, "digest": digest, "creator_id": draft["creator_user_id"]}).mappings().one()
    review_id = db.execute(text("""INSERT INTO reviews (org_id,revision_id,reviewer_user_id) VALUES (:org_id,:revision_id,:reviewer_id) RETURNING id"""), {"org_id": org_id, "revision_id": revision["id"], "reviewer_id": reviewer_id}).scalar_one()
    db.execute(text("""UPDATE drafts SET current_revision_id=:revision_id,state='processing',updated_at=now() WHERE org_id=:org_id AND id=:draft_id"""), {"revision_id": revision["id"], "org_id": org_id, "draft_id": draft["id"]})
    return {"revision_id": revision["id"], "revision_number": revision["revision_number"], "review_id": review_id}


def list_revisions(
    db: Session, *, org_id: UUID, draft_id: UUID, before: int | None, limit: int
) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT v.id AS revision_id, v.revision_number, v.title, v.channel,
               v.submitted_at, r.id AS review_id, r.state AS review_state,
               d.action AS decision
        FROM draft_revisions v
        JOIN reviews r ON r.org_id = v.org_id AND r.revision_id = v.id
        LEFT JOIN decisions d ON d.org_id = r.org_id AND d.review_id = r.id
        WHERE v.org_id = :org_id AND v.draft_id = :draft_id
          AND (:before IS NULL OR v.revision_number < :before)
        ORDER BY v.revision_number DESC
        LIMIT :limit
    """), {"org_id": org_id, "draft_id": draft_id, "before": before, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def revision_by_number(db: Session, *, org_id: UUID, draft_id: UUID, number: int) -> dict[str, Any] | None:
    row = db.execute(text("""SELECT v.id AS revision_id,v.revision_number,v.title,v.channel,v.content_nfc AS content,v.submitted_at,r.id AS review_id FROM draft_revisions v JOIN reviews r ON r.org_id=v.org_id AND r.revision_id=v.id WHERE v.org_id=:org_id AND v.draft_id=:draft_id AND v.revision_number=:number"""), {"org_id": org_id, "draft_id": draft_id, "number": number}).mappings().first()
    return dict(row) if row else None
