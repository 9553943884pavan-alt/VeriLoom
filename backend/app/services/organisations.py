"""Organisation membership, reviewer discovery, and invitation workflows."""

import base64
import binascii
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.repositories import organisations as repo
from app.schemas.organisations import CreateInvitationRequest, CreateOrganisationRequest


def _require_role(db: Session, org_id: UUID, user_id: UUID, allowed: set[str]) -> list[str]:
    roles = repo.active_role(db, org_id, user_id)
    if roles is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if not allowed.intersection(roles):
        raise ApiError(403, "forbidden", "You do not have access to this action.")
    return roles


def _decode_cursor(cursor: str | None) -> UUID | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        return UUID(bytes=raw)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ApiError(422, "invalid_cursor", "The page cursor is invalid.") from exc


def _page(rows: list[dict], limit: int, id_field: str) -> dict:
    has_more = len(rows) > limit
    items = rows[:limit]
    cursor = base64.urlsafe_b64encode(items[-1][id_field].bytes).rstrip(b"=").decode("ascii") if has_more else None
    return {"items": items, "next_cursor": cursor}


def create_organisation(db: Session, user_id: UUID, payload: CreateOrganisationRequest) -> dict:
    prefix = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-")[:48].rstrip("-") or "org"
    for _ in range(3):
        slug = f"{prefix}-{secrets.token_hex(4)}"
        try:
            result = repo.insert_organisation(db, name=payload.name, slug=slug, time_zone=payload.time_zone, user_id=user_id)
            db.commit()
            return result
        except IntegrityError:
            db.rollback()
    raise ApiError(503, "organisation_unavailable", "Could not create the organisation. Try again.")


def list_organisations(db: Session, user_id: UUID, cursor: str | None, limit: int) -> dict:
    rows = repo.list_user_organisations(db, user_id=user_id, after=_decode_cursor(cursor), limit=limit + 1)
    return _page(rows, limit, "org_id")


def list_members(db: Session, org_id: UUID, user_id: UUID, cursor: str | None, limit: int) -> dict:
    _require_role(db, org_id, user_id, {"admin"})
    rows = repo.list_members(db, org_id=org_id, after=_decode_cursor(cursor), limit=limit + 1)
    return _page(rows, limit, "user_id")


def list_reviewers(db: Session, org_id: UUID, user_id: UUID, cursor: str | None, limit: int) -> dict:
    _require_role(db, org_id, user_id, {"admin", "creator"})
    rows = repo.list_reviewers(db, org_id=org_id, current_user_id=user_id, after=_decode_cursor(cursor), limit=limit + 1)
    return _page(rows, limit, "user_id")


def update_member_roles(db: Session, org_id: UUID, actor_id: UUID, target_id: UUID, roles: list[str]) -> dict:
    if not repo.lock_organisation(db, org_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    _require_role(db, org_id, actor_id, {"admin"})
    target = repo.member_for_update(db, org_id=org_id, user_id=target_id)
    if target is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if target["status"] == "active" and "admin" in target["roles"] and "admin" not in roles:
        if repo.active_admin_count(db, org_id) <= 1:
            raise ApiError(409, "last_admin", "An organisation must retain an active administrator.")
    updated = repo.update_member_roles(db, org_id=org_id, user_id=target_id, roles=roles)
    db.commit()
    return updated


def _invitation_hash(settings: Settings, token: str) -> bytes:
    secret = settings.SESSION_SECRET.get_secret_value().encode("utf-8")
    return hmac.digest(secret, b"invitation:v1:" + token.encode("utf-8"), "sha256")


def create_invitation(db: Session, settings: Settings, org_id: UUID, actor_id: UUID, payload: CreateInvitationRequest) -> dict:
    _require_role(db, org_id, actor_id, {"admin"})
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)
    repo.insert_invitation(db, org_id=org_id, email=payload.email, role=payload.role, token_hash=_invitation_hash(settings, token), invited_by_user_id=actor_id, expires_at=expires_at)
    db.commit()
    return {"invitation_token": token, "expires_at": expires_at}


def accept_invitation(db: Session, settings: Settings, user: dict, token: str) -> dict:
    invitation = repo.invitation_for_update(db, _invitation_hash(settings, token))
    now = datetime.now(timezone.utc)
    if invitation is None or invitation["accepted_at"] is not None or invitation["expires_at"] <= now or invitation["email"] != user["email"]:
        raise ApiError(404, "invalid_invitation", "This invitation is invalid or expired.")
    org_id = invitation["org_id"]
    if repo.active_role(db, org_id, user["id"]) is not None:
        raise ApiError(409, "already_member", "You already belong to this organisation.")
    try:
        repo.insert_membership(db, org_id=org_id, user_id=user["id"], roles=invitation["roles"])
        repo.mark_invitation_accepted(db, invitation["id"])
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "already_member", "You already belong to this organisation.") from exc
    return {"org_id": org_id, "roles": invitation["roles"]}
