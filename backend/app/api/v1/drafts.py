"""Creator draft and revision endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.api.v1.organisations import signed_in_user
from app.core.errors import ApiError
from app.db.session import get_session
from app.schemas.drafts import DraftCreate, DraftPatch, DraftSubmit, SubmissionAccepted
from app.services import drafts

router = APIRouter(prefix="/organisations", tags=["drafts"])


def _key(value: str | None) -> UUID:
    try:
        return UUID(value) if value else (_ for _ in ()).throw(ValueError())
    except ValueError as exc:
        raise ApiError(422, "invalid_idempotency_key", "Idempotency-Key must be a UUID.") from exc


@router.post("/{org_id}/drafts", status_code=status.HTTP_201_CREATED)
def create_draft(org_id: UUID, payload: DraftCreate, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return drafts.create(db, org_id=org_id, user_id=user["id"], payload=payload)


@router.get("/{org_id}/drafts")
def list_drafts(org_id: UUID, view: str = "mine", cursor: str | None = None, status_filter: str | None = Query(None, alias="status"), limit: int = Query(20, ge=1, le=100), user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return drafts.listing(db, org_id=org_id, user_id=user["id"], view=view, state=status_filter, cursor=cursor, limit=limit)


@router.get("/{org_id}/drafts/{draft_id}")
def get_draft(org_id: UUID, draft_id: UUID, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return drafts.detail(db, org_id=org_id, draft_id=draft_id, user_id=user["id"])


@router.patch("/{org_id}/drafts/{draft_id}")
def patch_draft(org_id: UUID, draft_id: UUID, payload: DraftPatch, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return drafts.edit(db, org_id=org_id, draft_id=draft_id, user_id=user["id"], payload=payload)


@router.post("/{org_id}/drafts/{draft_id}/submissions", status_code=status.HTTP_202_ACCEPTED, response_model=SubmissionAccepted)
def submit_draft(org_id: UUID, draft_id: UUID, payload: DraftSubmit, key: Annotated[str | None, Header(alias="Idempotency-Key")] = None, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return drafts.submit(db, org_id=org_id, draft_id=draft_id, user_id=user["id"], payload=payload, idempotency_key=_key(key))


@router.get("/{org_id}/drafts/{draft_id}/revisions")
def list_revisions(
    org_id: UUID, draft_id: UUID, cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(signed_in_user), db: Session = Depends(get_session),
) -> dict:
    return drafts.revisions(
        db, org_id=org_id, draft_id=draft_id, user_id=user["id"],
        cursor=cursor, limit=limit,
    )


@router.get("/{org_id}/drafts/{draft_id}/revisions/{revision_number}")
def get_revision(org_id: UUID, draft_id: UUID, revision_number: int, user: dict = Depends(signed_in_user), db: Session = Depends(get_session)) -> dict:
    return drafts.revision(db, org_id=org_id, draft_id=draft_id, number=revision_number, user_id=user["id"])
