"""Manually shared, email-bound team invitation routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.organisations import signed_in_user
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.organisations import (
    AcceptInvitationRequest,
    AcceptedInvitation,
    CreateInvitationRequest,
    InvitationCreated,
)
from app.services import organisations


router = APIRouter(tags=["invitations"])


@router.post(
    "/organisations/{org_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationCreated,
)
def create_invitation(
    org_id: UUID,
    payload: CreateInvitationRequest,
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return organisations.create_invitation(db, settings, org_id, user["id"], payload)


@router.post("/invitations/accept", response_model=AcceptedInvitation)
def accept_invitation(
    payload: AcceptInvitationRequest,
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return organisations.accept_invitation(db, settings, user, payload.invitation_token)
