"""Reviewer read access and atomic human decisions."""

import hashlib
import json
import base64
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder

from app.core.errors import ApiError
from app.db.repositories import reviews as repo
from app.schemas.drafts import DecisionRequest
from app.schemas.reviews import QuestionRequest
from app.jobs.queue import enqueue_job
from app.services.organisations import _require_role


def _participant(db: Session, org_id: UUID, review_id: UUID, user_id: UUID) -> dict:
    row = repo.get_review(db, org_id=org_id, review_id=review_id)
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    roles = _require_role(db, org_id, user_id, {"creator", "reviewer"})
    if not ((row["creator_user_id"] == user_id and "creator" in roles) or (row["assigned_reviewer_user_id"] == user_id and "reviewer" in roles)):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    return row


def get(db: Session, *, org_id: UUID, review_id: UUID, user_id: UUID) -> dict:
    row = _participant(db, org_id, review_id, user_id)
    failure = {"code":row["error_code"],"message":"The automated review could not be completed.","retryable":True} if row["state"] == "failed" else None
    findings = repo.findings(db, org_id=org_id, review_id=review_id)
    for finding in findings:
        start, end, quote = finding.pop("anchor_start"), finding.pop("anchor_end"), finding.pop("quote")
        finding["anchor"] = {"start_codepoint":start,"end_codepoint":end,"quote":quote} if start is not None else None
    return {k:v for k,v in {**row,"findings":findings,"failure":failure}.items()
            if k not in {"creator_user_id", "error_code", "current_revision_id"}}


def add_comment(db: Session, *, org_id: UUID, review_id: UUID, user_id: UUID, revision_id: UUID, body: str) -> dict:
    row = _participant(db, org_id, review_id, user_id)
    if revision_id != row["revision_id"]:
        raise ApiError(409, "stale_revision", "Comments must refer to this review's revision.")
    if row["state"] not in {"ready_for_review", "changes_requested"}:
        raise ApiError(409, "review_read_only", "Comments are not accepted in this review state.")
    result = repo.insert_comment(db, org_id=org_id, review_id=review_id, revision_id=revision_id, actor_id=user_id, body=body)
    db.commit()
    return result


def _cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        created_at, item_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return datetime.fromisoformat(created_at), UUID(item_id)
    except (ValueError, TypeError, IndexError) as exc:
        raise ApiError(422, "invalid_cursor", "The page cursor is invalid.") from exc


def _page(rows: list[dict], limit: int, field: str) -> dict:
    more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (base64.urlsafe_b64encode(json.dumps(
        [items[-1]["created_at"].isoformat(), str(items[-1][field])], separators=(",", ":")
    ).encode()).rstrip(b"=").decode() if more else None)
    return {"items": items, "next_cursor": next_cursor}


def comments_page(db: Session, *, org_id: UUID, review_id: UUID, user_id: UUID,
                  cursor: str | None, limit: int) -> dict:
    _participant(db, org_id, review_id, user_id)
    return _page(repo.list_comments(db, org_id=org_id, review_id=review_id,
                                   after=_cursor(cursor), limit=limit + 1), limit, "comment_id")


def ask_question(db: Session, *, org_id: UUID, review_id: UUID, user_id: UUID,
                 payload: QuestionRequest, idempotency_key: UUID) -> dict:
    _require_role(db, org_id, user_id, {"reviewer"})
    endpoint = "/organisations/{org_id}/reviews/{review_id}/questions"
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
    digest = hashlib.sha256((f"{org_id}\0{review_id}\0" + body).encode()).hexdigest()
    prior = db.execute(text("SELECT org_id,request_sha256,response_json,expires_at<=now() expired FROM idempotency_records WHERE user_id=:user_id AND endpoint=:endpoint AND key=:key"), {"user_id":user_id,"endpoint":endpoint,"key":idempotency_key}).mappings().first()
    if prior:
        db.rollback()
        if prior["expired"] or prior["org_id"] != org_id or prior["request_sha256"] != digest:
            raise ApiError(409, "idempotency_key_reused", "This key cannot be used for this request.")
        return dict(prior["response_json"])
    row = repo.get_review(db, org_id=org_id, review_id=review_id)
    if row is None or row["assigned_reviewer_user_id"] != user_id:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if row["state"] != "ready_for_review" or row["current_revision_id"] != payload.revision_id:
        raise ApiError(409, "stale_revision", "Questions require the current ready review revision.")
    if payload.finding_id is not None and not repo.finding_belongs_to_review(db, org_id=org_id, review_id=review_id, finding_id=payload.finding_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    turn_id = repo.insert_question(db, org_id=org_id, review_id=review_id, actor_id=user_id,
                                  finding_id=payload.finding_id, question=payload.question)
    enqueue_job(db, org_id=org_id, kind="answer_question", resource_id=turn_id)
    result = {"turn_id":turn_id,"state":"queued","status_url":f"/api/v1/organisations/{org_id}/reviews/{review_id}/questions/{turn_id}"}
    db.execute(text("""INSERT INTO idempotency_records(user_id,org_id,endpoint,key,request_sha256,response_status,response_json,resource_id,state,expires_at)
        VALUES (:user_id,:org_id,:endpoint,:key,:digest,202,CAST(:response AS jsonb),:resource,'complete',now()+interval '24 hours')"""),
        {"user_id":user_id,"org_id":org_id,"endpoint":endpoint,"key":idempotency_key,"digest":digest,
         "response":json.dumps({**result,"turn_id":str(turn_id)}),"resource":turn_id})
    db.commit()
    return result


def questions_page(db: Session, *, org_id: UUID, review_id: UUID, user_id: UUID,
                   cursor: str | None, limit: int) -> dict:
    review = _participant(db, org_id, review_id, user_id)
    rows = repo.list_questions(db, org_id=org_id, review_id=review_id, after=_cursor(cursor), limit=limit+1)
    for row in rows:
        row["revision_id"] = review["revision_id"]
        full = repo.get_question(db, org_id=org_id, review_id=review_id, turn_id=row["turn_id"])
        row["citations"] = full["citations"] if full else []
    return _page(rows, limit, "turn_id")


def question(db: Session, *, org_id: UUID, review_id: UUID, turn_id: UUID, user_id: UUID) -> dict:
    row = _participant(db, org_id, review_id, user_id)
    result = repo.get_question(db, org_id=org_id, review_id=review_id, turn_id=turn_id)
    if result is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    result["revision_id"] = row["revision_id"]
    return result


def retry(db: Session, *, org_id: UUID, review_id: UUID, user_id: UUID, idempotency_key: UUID) -> dict:
    _participant(db, org_id, review_id, user_id)
    endpoint = "/organisations/{org_id}/reviews/{review_id}/retry"
    digest = hashlib.sha256(f"{org_id}\0{review_id}\0{{}}".encode()).hexdigest()
    prior = db.execute(text("SELECT org_id,request_sha256,response_json,expires_at<=now() expired FROM idempotency_records WHERE user_id=:user_id AND endpoint=:endpoint AND key=:key"), {"user_id":user_id,"endpoint":endpoint,"key":idempotency_key}).mappings().first()
    if prior:
        db.rollback()
        if prior["expired"] or prior["org_id"] != org_id or prior["request_sha256"] != digest:
            raise ApiError(409, "idempotency_key_reused", "This key cannot be used for this request.")
        return dict(prior["response_json"])
    locked = repo.current_review_retry(db, org_id=org_id, review_id=review_id)
    if locked is None or (locked["creator_user_id"] != user_id and locked["reviewer_user_id"] != user_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if locked["state"] != "failed" or locked["revision_id"] != locked["current_revision_id"]:
        raise ApiError(409, "review_not_retryable", "Only a failed review for the current revision can be retried.")
    count = db.execute(text("SELECT count(*) FROM jobs WHERE org_id=:org_id AND kind='review_revision' AND resource_id=:review_id"), {"org_id":org_id,"review_id":review_id}).scalar_one()
    if count >= 3:
        raise ApiError(409, "retry_limit_reached", "This review has reached its retry limit.")
    db.execute(text("UPDATE reviews SET state='queued',error_code=NULL,completed_at=NULL,updated_at=now() WHERE org_id=:org_id AND id=:review_id"), {"org_id":org_id,"review_id":review_id})
    db.execute(text("UPDATE drafts SET state='processing',updated_at=now() WHERE org_id=:org_id AND id=:draft_id"), {"org_id":org_id,"draft_id":locked["draft_id"]})
    enqueue_job(db, org_id=org_id, kind="review_revision", resource_id=review_id)
    result = {"review_id":review_id,"state":"queued","status_url":f"/api/v1/organisations/{org_id}/reviews/{review_id}"}
    db.execute(text("""INSERT INTO idempotency_records(user_id,org_id,endpoint,key,request_sha256,response_status,response_json,resource_id,state,expires_at)
        VALUES (:user_id,:org_id,:endpoint,:key,:digest,202,CAST(:response AS jsonb),:resource,'complete',now()+interval '24 hours')"""),
        {"user_id":user_id,"org_id":org_id,"endpoint":endpoint,"key":idempotency_key,"digest":digest,
         "response":json.dumps({**result,"review_id":str(review_id)}),"resource":review_id})
    db.commit()
    return result


def decide(db: Session, *, org_id: UUID, review_id: UUID, user_id: UUID, payload: DecisionRequest, idempotency_key: UUID) -> dict:
    _require_role(db, org_id, user_id, {"reviewer"})
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
    digest = hashlib.sha256((f"{org_id}\0{review_id}\0"+body).encode()).hexdigest()
    endpoint = "/organisations/{org_id}/reviews/{review_id}/decisions"
    prior = db.execute(text("SELECT org_id,request_sha256,response_json,expires_at<=now() expired FROM idempotency_records WHERE user_id=:user_id AND endpoint=:endpoint AND key=:key"), {"user_id":user_id,"endpoint":endpoint,"key":idempotency_key}).mappings().first()
    if prior:
        db.rollback()
        if prior["expired"] or prior["org_id"] != org_id or prior["request_sha256"] != digest:
            raise ApiError(409, "idempotency_key_reused", "This key cannot be used for this request.")
        return dict(prior["response_json"])
    row = repo.lock_review_decision(db, org_id=org_id, review_id=review_id)
    if row is None or row["reviewer_user_id"] != user_id:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if row["creator_user_id"] == user_id:
        raise ApiError(403, "self_approval_forbidden", "A Creator cannot decide their own review.")
    if row["review_version"] != payload.expected_review_version or row["revision_id"] != payload.expected_revision_id or row["current_revision_id"] != row["revision_id"]:
        raise ApiError(409, "stale_version", "The review changed. Refresh before deciding.", {"current_review_version":row["review_version"]})
    if row["state"] != "ready_for_review":
        raise ApiError(409, "review_not_decidable", "Only a completed review can receive a decision.")
    if payload.action == "request_changes" and not (payload.comment and payload.comment.strip()):
        raise ApiError(422, "comment_required", "A non-empty comment is required to request changes.")
    if payload.action == "approve" and repo.has_override_trigger(db, org_id=org_id, review_id=review_id) and (not payload.override_reason or len(payload.override_reason.strip()) < 15):
        raise ApiError(422, "override_reason_required", "Explain the override in at least 15 characters.")
    review_state, draft_state = ("changes_requested","changes_requested") if payload.action == "request_changes" else ("approved","approved")
    result = repo.insert_decision(db, org_id=org_id, review_id=review_id, draft_id=row["draft_id"], actor_id=user_id, action=payload.action, comment=payload.comment, override_reason=payload.override_reason, expected=payload.expected_review_version, review_state=review_state, draft_state=draft_state)
    result["revision_id"] = row["revision_id"]
    result["review_state"] = review_state
    result["draft_state"] = draft_state
    result["review_version"] = row["review_version"]+1
    db.execute(text("""INSERT INTO idempotency_records(user_id,org_id,endpoint,key,request_sha256,response_status,response_json,resource_id,state,expires_at) VALUES (:user_id,:org_id,:endpoint,:key,:digest,201,CAST(:response AS jsonb),:resource,'complete',now()+interval '24 hours')"""), {"user_id":user_id,"org_id":org_id,"endpoint":endpoint,"key":idempotency_key,"digest":digest,"response":json.dumps(jsonable_encoder(result)),"resource":result["decision_id"]})
    db.commit()
    return result
