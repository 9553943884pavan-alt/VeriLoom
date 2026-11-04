"""Public organisation, membership, and invitation shapes."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from email_validator import EmailNotValidError, validate_email
from pydantic import Field, field_validator

from app.schemas.auth import StrictModel


Role = Literal["admin", "creator", "reviewer"]


class CreateOrganisationRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    time_zone: str = "UTC"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Organisation name cannot be blank")
        return value

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a valid IANA time zone") from exc
        return value


class OrganisationCreated(StrictModel):
    org_id: UUID
    name: str
    time_zone: str
    roles: list[Role]


class OrganisationSummary(StrictModel):
    org_id: UUID
    name: str
    roles: list[Role]


class OrganisationList(StrictModel):
    items: list[OrganisationSummary]
    next_cursor: str | None


class MemberSummary(StrictModel):
    user_id: UUID
    email: str
    display_name: str
    roles: list[Role]
    status: Literal["active", "disabled"]


class MemberList(StrictModel):
    items: list[MemberSummary]
    next_cursor: str | None


class ReviewerSummary(StrictModel):
    user_id: UUID
    display_name: str


class ReviewerList(StrictModel):
    items: list[ReviewerSummary]
    next_cursor: str | None


class CreateInvitationRequest(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["creator", "reviewer"]
    expires_in_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError("Enter a valid email address") from exc


class InvitationCreated(StrictModel):
    invitation_token: str
    expires_at: datetime


class AcceptInvitationRequest(StrictModel):
    invitation_token: str = Field(min_length=20, max_length=256)


class AcceptedInvitation(StrictModel):
    org_id: UUID
    roles: list[Role]


class UpdateMemberRolesRequest(StrictModel):
    roles: list[Role] = Field(min_length=1, max_length=3)

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, value: list[Role]) -> list[Role]:
        if len(value) != len(set(value)):
            raise ValueError("Roles cannot be repeated")
        return value
