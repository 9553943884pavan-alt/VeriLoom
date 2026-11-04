"""Tenant-scoped organisation, membership, and invitation SQL."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def active_role(db: Session, org_id: UUID, user_id: UUID) -> list[str] | None:
    row = db.execute(text("SELECT roles FROM memberships WHERE org_id = :org_id AND user_id = :user_id AND status = 'active'"), {"org_id": org_id, "user_id": user_id}).mappings().first()
    return list(row["roles"]) if row else None


def insert_organisation(db: Session, *, name: str, slug: str, time_zone: str, user_id: UUID) -> dict[str, Any]:
    row = db.execute(text("INSERT INTO organisations (name, slug, time_zone) VALUES (:name, :slug, :time_zone) RETURNING id, name, time_zone"), {"name": name, "slug": slug, "time_zone": time_zone}).mappings().one()
    db.execute(text("INSERT INTO memberships (org_id, user_id, roles, status) VALUES (:org_id, :user_id, ARRAY['admin']::text[], 'active')"), {"org_id": row["id"], "user_id": user_id})
    return {"org_id": row["id"], "name": row["name"], "time_zone": row["time_zone"], "roles": ["admin"]}


def list_user_organisations(db: Session, *, user_id: UUID, after: UUID | None, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT o.id AS org_id, o.name, m.roles FROM memberships AS m
        JOIN organisations AS o ON o.id = m.org_id
        WHERE m.user_id = :user_id AND m.status = 'active'
          AND (CAST(:after AS uuid) IS NULL OR o.id > CAST(:after AS uuid))
        ORDER BY o.id LIMIT :limit
    """), {"user_id": user_id, "after": after, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def list_members(db: Session, *, org_id: UUID, after: UUID | None, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT m.user_id, u.email, u.display_name, m.roles, m.status
        FROM memberships AS m JOIN users AS u ON u.id = m.user_id
        WHERE m.org_id = :org_id AND (CAST(:after AS uuid) IS NULL OR m.user_id > CAST(:after AS uuid))
        ORDER BY m.user_id LIMIT :limit
    """), {"org_id": org_id, "after": after, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def list_reviewers(db: Session, *, org_id: UUID, current_user_id: UUID, after: UUID | None, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT m.user_id, u.display_name FROM memberships AS m
        JOIN users AS u ON u.id = m.user_id
        WHERE m.org_id = :org_id AND m.status = 'active'
          AND m.roles @> ARRAY['reviewer']::text[]
          AND m.user_id <> :current_user_id AND u.disabled_at IS NULL
          AND (CAST(:after AS uuid) IS NULL OR m.user_id > CAST(:after AS uuid))
        ORDER BY m.user_id LIMIT :limit
    """), {"org_id": org_id, "current_user_id": current_user_id, "after": after, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def lock_organisation(db: Session, org_id: UUID) -> bool:
    return db.execute(text("SELECT id FROM organisations WHERE id = :org_id FOR UPDATE"), {"org_id": org_id}).scalar_one_or_none() is not None


def member_for_update(db: Session, *, org_id: UUID, user_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("SELECT user_id, roles, status FROM memberships WHERE org_id = :org_id AND user_id = :user_id FOR UPDATE"), {"org_id": org_id, "user_id": user_id}).mappings().first()
    return dict(row) if row else None


def active_admin_count(db: Session, org_id: UUID) -> int:
    return db.execute(text("SELECT count(*) FROM memberships WHERE org_id = :org_id AND status = 'active' AND roles @> ARRAY['admin']::text[]"), {"org_id": org_id}).scalar_one()


def update_member_roles(db: Session, *, org_id: UUID, user_id: UUID, roles: list[str]) -> dict[str, Any]:
    row = db.execute(text("""
        UPDATE memberships SET roles = :roles WHERE org_id = :org_id AND user_id = :user_id
        RETURNING user_id, roles, status
    """), {"org_id": org_id, "user_id": user_id, "roles": roles}).mappings().one()
    user = db.execute(text("SELECT email, display_name FROM users WHERE id = :user_id"), {"user_id": user_id}).mappings().one()
    return {**dict(row), **dict(user)}


def insert_invitation(db: Session, *, org_id: UUID, email: str, role: str, token_hash: bytes, invited_by_user_id: UUID, expires_at: datetime) -> None:
    db.execute(text("""
        INSERT INTO invitations (org_id, email, roles, token_hash, invited_by_user_id, expires_at)
        VALUES (:org_id, :email, :roles, :token_hash, :invited_by_user_id, :expires_at)
    """), {"org_id": org_id, "email": email, "roles": [role], "token_hash": token_hash, "invited_by_user_id": invited_by_user_id, "expires_at": expires_at})


def invitation_for_update(db: Session, token_hash: bytes) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT id, org_id, email, roles, expires_at, accepted_at
        FROM invitations WHERE token_hash = :token_hash FOR UPDATE
    """), {"token_hash": token_hash}).mappings().first()
    return dict(row) if row else None


def insert_membership(db: Session, *, org_id: UUID, user_id: UUID, roles: list[str]) -> None:
    db.execute(text("INSERT INTO memberships (org_id, user_id, roles, status) VALUES (:org_id, :user_id, :roles, 'active')"), {"org_id": org_id, "user_id": user_id, "roles": roles})


def mark_invitation_accepted(db: Session, invitation_id: UUID) -> None:
    db.execute(text("UPDATE invitations SET accepted_at = now() WHERE id = :id"), {"id": invitation_id})
