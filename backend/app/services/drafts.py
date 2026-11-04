"""Creator draft lifecycle and scoped revision access."""

import base64
import hashlib
import json
import unicodedata
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.repositories import drafts as repo
from app.jobs.queue import enqueue_job
from app.schemas.drafts import DraftCreate, DraftPatch, DraftSubmit
from app.services.organisations import _require_role


def _cursor(cursor: str | None) -> UUID | None:
    if cursor is None:
        return None
    try:
        return UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as exc:
        raise ApiError(422, "invalid_cursor", "The page cursor is invalid.") from exc


def _page(items: list[dict], limit: int) -> dict:
    more = len(items) > limit
    visible = items[:limit]
    cursor = base64.urlsafe_b64encode(visible[-1]["draft_id"].bytes).rstrip(b"=").decode() if more else None
    return {"items": visible, "next_cursor": cursor}


def create(db: Session, *, org_id: UUID, user_id: UUID, payload: DraftCreate) -> dict:
    _require_role(db, org_id, user_id, {"creator"})
    result = repo.insert_draft(db, org_id=org_id, user_id=user_id, title=payload.title, channel=payload.channel, content=unicodedata.normalize("NFC", payload.content))
    db.commit()
    return result


def listing(db: Session, *, org_id: UUID, user_id: UUID, view: str, state: str | None, cursor: str | None, limit: int) -> dict:
    if view not in {"mine", "assigned"}:
        raise ApiError(422, "invalid_view", "Choose mine or assigned.")
    role = "creator" if view == "mine" else "reviewer"
    _require_role(db, org_id, user_id, {role})
    return _page(repo.list_drafts(db, org_id=org_id, user_id=user_id, view=view, state=state, after=_cursor(cursor), limit=limit + 1), limit)


def detail(db: Session, *, org_id: UUID, draft_id: UUID, user_id: UUID) -> dict:
    draft = repo.get_draft(db, org_id=org_id, draft_id=draft_id)
    if draft is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    roles = _require_role(db, org_id, user_id, {"creator", "reviewer"})
    owner = draft["creator_user_id"] == user_id and "creator" in roles
    assigned = draft.get("assigned_reviewer_user_id") == user_id and "reviewer" in roles
    if not owner and not assigned:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if not owner:
        draft.pop("working_content", None)
    return draft


def edit(db: Session, *, org_id: UUID, draft_id: UUID, user_id: UUID, payload: DraftPatch) -> dict:
    _require_role(db, org_id, user_id, {"creator"})
    fields = {key: value for key, value in {"title":payload.title,"channel":payload.channel,"working_content":unicodedata.normalize("NFC", payload.content) if payload.content is not None else None}.items() if value is not None}
    if not fields:
        raise ApiError(422, "empty_update", "Provide at least one field to update.")
    updated = repo.update_draft(db, org_id=org_id, draft_id=draft_id, user_id=user_id, expected=payload.expected_working_copy_version, fields=fields)
    if updated is None:
        current = repo.get_draft(db, org_id=org_id, draft_id=draft_id)
        if current is None or current["creator_user_id"] != user_id:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        if current["working_copy_version"] != payload.expected_working_copy_version:
            raise ApiError(409, "stale_version", "The working copy changed. Refresh before saving.", {"current_working_copy_version":current["working_copy_version"]})
        raise ApiError(409, "draft_not_editable", "This draft cannot be edited in its current state.")
    db.commit()
    return updated


def submit(db: Session, *, org_id: UUID, draft_id: UUID, user_id: UUID, payload: DraftSubmit, idempotency_key: UUID) -> dict:
    _require_role(db, org_id, user_id, {"creator"})
    body = json.dumps({
        "expected_working_copy_version": payload.expected_working_copy_version,
        "reviewer_user_id": str(payload.reviewer_user_id),
    }, sort_keys=True)
    digest = hashlib.sha256((f"{org_id}\0{draft_id}\0" + body).encode()).hexdigest()
    endpoint = "/organisations/{org_id}/drafts/{draft_id}/submissions"

    def replay_if_present() -> dict | None:
        row = db.execute(text("""
            SELECT org_id, request_sha256, response_json, expires_at <= now() AS expired
            FROM idempotency_records
            WHERE user_id = :user_id AND endpoint = :endpoint AND key = :key
        """), {"user_id": user_id, "endpoint": endpoint, "key": idempotency_key}).mappings().first()
        if row is None:
            return None
        db.rollback()
        if row["expired"] or row["org_id"] != org_id or row["request_sha256"] != digest:
            raise ApiError(409, "idempotency_key_reused", "This key cannot be used for this request.")
        return dict(row["response_json"])

    existing = replay_if_present()
    if existing is not None:
        return existing
    draft = repo.lock_draft_for_submit(db, org_id=org_id, draft_id=draft_id)
    if draft is None or draft["creator_user_id"] != user_id:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    # A second identical request can wait on the draft lock while the first
    # submission commits. Recheck the key after that wait, before state checks.
    existing = replay_if_present()
    if existing is not None:
        return existing
    if draft["working_copy_version"] != payload.expected_working_copy_version:
        raise ApiError(409, "stale_version", "The working copy changed. Refresh before submitting.", {"current_working_copy_version":draft["working_copy_version"]})
    if draft["state"] not in {"editing", "changes_requested"}:
        raise ApiError(409, "draft_not_submittable", "This draft cannot be submitted in its current state.")
    if payload.reviewer_user_id == user_id or not repo.reviewer_is_active(db, org_id=org_id, user_id=payload.reviewer_user_id):
        raise ApiError(422, "invalid_reviewer", "Choose another active organisation reviewer.")
    content = unicodedata.normalize("NFC", draft["working_content"])
    created = repo.create_submission(db, org_id=org_id, draft=draft, reviewer_id=payload.reviewer_user_id, content=content, digest=hashlib.sha256(content.encode()).hexdigest())
    enqueue_job(db, org_id=org_id, kind="review_revision", resource_id=created["review_id"])
    result = {**created,"draft_state":"processing","review_state":"queued","status_url":f"/api/v1/organisations/{org_id}/reviews/{created['review_id']}"}
    db.execute(text("""INSERT INTO idempotency_records(user_id,org_id,endpoint,key,request_sha256,response_status,response_json,resource_id,state,expires_at) VALUES (:user_id,:org_id,:endpoint,:key,:digest,202,CAST(:response AS jsonb),:resource,'complete',now()+interval '24 hours')"""), {"user_id":user_id,"org_id":org_id,"endpoint":endpoint,"key":idempotency_key,"digest":digest,"response":json.dumps({**result,**{k:str(v) for k,v in created.items()}}),"resource":created["review_id"]})
    db.commit()
    return result


def revisions(
    db: Session, *, org_id: UUID, draft_id: UUID, user_id: UUID,
    cursor: str | None, limit: int,
) -> dict:
    detail(db, org_id=org_id, draft_id=draft_id, user_id=user_id)
    try:
        before = int(cursor) if cursor is not None else None
        if before is not None and before < 1:
            raise ValueError
    except ValueError as exc:
        raise ApiError(422, "invalid_cursor", "The page cursor is invalid.") from exc
    rows = repo.list_revisions(
        db, org_id=org_id, draft_id=draft_id, before=before, limit=limit + 1,
    )
    visible = rows[:limit]
    return {
        "items": visible,
        "next_cursor": str(visible[-1]["revision_number"]) if len(rows) > limit else None,
    }


def revision(db: Session, *, org_id: UUID, draft_id: UUID, number: int, user_id: UUID) -> dict:
    detail(db, org_id=org_id, draft_id=draft_id, user_id=user_id)
    result = repo.revision_by_number(db, org_id=org_id, draft_id=draft_id, number=number)
    if result is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    return result
