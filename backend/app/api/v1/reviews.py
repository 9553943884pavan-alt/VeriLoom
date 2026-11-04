"""Reviewer review inspection, comment, and decision endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.api.v1.drafts import _key
from app.api.v1.organisations import signed_in_user
from app.db.session import get_session
from app.schemas.drafts import CommentRequest, DecisionRequest
from app.schemas.reviews import QuestionAccepted, QuestionRequest, RetryAccepted
from app.services import reviews

router = APIRouter(prefix="/organisations", tags=["reviews"])


@router.get("/{org_id}/reviews/{review_id}")
def get_review(org_id: UUID, review_id: UUID, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.get(db, org_id=org_id, review_id=review_id, user_id=user["id"])


@router.post("/{org_id}/reviews/{review_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(org_id: UUID, review_id: UUID, payload: CommentRequest, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.add_comment(db, org_id=org_id, review_id=review_id, user_id=user["id"], revision_id=payload.revision_id, body=payload.body)


@router.get("/{org_id}/reviews/{review_id}/comments")
def list_comments(org_id: UUID, review_id: UUID, cursor: str | None = None, limit: int = Query(50, ge=1, le=100), user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.comments_page(db, org_id=org_id, review_id=review_id, user_id=user["id"], cursor=cursor, limit=limit)


@router.post("/{org_id}/reviews/{review_id}/questions", status_code=status.HTTP_202_ACCEPTED, response_model=QuestionAccepted)
def ask_question(org_id: UUID, review_id: UUID, payload: QuestionRequest, key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.ask_question(db, org_id=org_id, review_id=review_id, user_id=user["id"], payload=payload, idempotency_key=_key(key))


@router.get("/{org_id}/reviews/{review_id}/questions")
def list_questions(org_id: UUID, review_id: UUID, cursor: str | None = None, limit: int = Query(50, ge=1, le=100), user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.questions_page(db, org_id=org_id, review_id=review_id, user_id=user["id"], cursor=cursor, limit=limit)


@router.get("/{org_id}/reviews/{review_id}/questions/{turn_id}")
def get_question(org_id: UUID, review_id: UUID, turn_id: UUID, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.question(db, org_id=org_id, review_id=review_id, turn_id=turn_id, user_id=user["id"])


@router.post("/{org_id}/reviews/{review_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=RetryAccepted)
def retry_review(org_id: UUID, review_id: UUID, key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.retry(db, org_id=org_id, review_id=review_id, user_id=user["id"], idempotency_key=_key(key))


@router.post("/{org_id}/reviews/{review_id}/decisions", status_code=status.HTTP_201_CREATED)
def decide(org_id: UUID, review_id: UUID, payload: DecisionRequest, key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return reviews.decide(db, org_id=org_id, review_id=review_id, user_id=user["id"], payload=payload, idempotency_key=_key(key))
