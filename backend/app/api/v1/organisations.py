"""Organisation creation, membership, and reviewer discovery routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import cookie_name
from app.db.session import get_session
from app.schemas.organisations import (
    CreateOrganisationRequest,
    MemberList,
    MemberSummary,
    OrganisationCreated,
    OrganisationList,
    ReviewerList,
    UpdateMemberRolesRequest,
)
from app.services import authentication, organisations


router = APIRouter(prefix="/organisations", tags=["organisations"])


def signed_in_user(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return authentication.current_user(db, settings, request.cookies.get(cookie_name(settings)))


@router.get("", response_model=OrganisationList)
def list_organisations(
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
) -> dict:
    return organisations.list_organisations(db, user["id"], cursor, limit)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrganisationCreated)
def create_organisation(
    payload: CreateOrganisationRequest,
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
) -> dict:
    return organisations.create_organisation(db, user["id"], payload)


@router.get("/{org_id}/members", response_model=MemberList)
def list_members(
    org_id: UUID,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
) -> dict:
    return organisations.list_members(db, org_id, user["id"], cursor, limit)


@router.get("/{org_id}/reviewers", response_model=ReviewerList)
def list_reviewers(
    org_id: UUID,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
) -> dict:
    return organisations.list_reviewers(db, org_id, user["id"], cursor, limit)


@router.patch("/{org_id}/members/{user_id}", response_model=MemberSummary)
def update_member_roles(
    org_id: UUID,
    user_id: UUID,
    payload: UpdateMemberRolesRequest,
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
) -> dict:
    return organisations.update_member_roles(db, org_id, user["id"], user_id, payload.roles)
