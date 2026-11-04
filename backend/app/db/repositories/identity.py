"""Parameterized identity and session queries."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def find_user_by_email(db: Session, email: str) -> dict[str, Any] | None:
    row = db.execute(text("SELECT id, email, display_name, password_hash, disabled_at FROM users WHERE email = :email"), {"email": email}).mappings().first()
    return dict(row) if row else None


def insert_user(db: Session, *, email: str, display_name: str, password_hash: str) -> dict[str, Any]:
    row = db.execute(
        text("INSERT INTO users (email, display_name, password_hash) VALUES (:email, :display_name, :password_hash) RETURNING id, email, display_name"),
        {"email": email, "display_name": display_name, "password_hash": password_hash},
    ).mappings().one()
    return dict(row)


def list_active_memberships(db: Session, user_id: UUID) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT m.org_id, o.name, m.roles FROM memberships AS m
        JOIN organisations AS o ON o.id = m.org_id
        WHERE m.user_id = :user_id AND m.status = 'active'
        ORDER BY o.name, m.org_id
    """), {"user_id": user_id}).mappings().all()
    return [dict(row) for row in rows]


def insert_session(db: Session, *, user_id: UUID, token_hash: bytes, expires_at: datetime) -> None:
    db.execute(text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:user_id, :token_hash, :expires_at)"), {"user_id": user_id, "token_hash": token_hash, "expires_at": expires_at})


def find_session_user(db: Session, token_hash: bytes) -> dict[str, Any] | None:
    row = db.execute(text("""
        SELECT u.id, u.email, u.display_name FROM sessions AS s
        JOIN users AS u ON u.id = s.user_id
        WHERE s.token_hash = :token_hash AND s.revoked_at IS NULL
          AND s.expires_at > now() AND u.disabled_at IS NULL
    """), {"token_hash": token_hash}).mappings().first()
    return dict(row) if row else None


def revoke_session(db: Session, token_hash: bytes) -> None:
    db.execute(text("UPDATE sessions SET revoked_at = now() WHERE token_hash = :token_hash AND revoked_at IS NULL"), {"token_hash": token_hash})
