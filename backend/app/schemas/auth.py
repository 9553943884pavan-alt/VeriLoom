"""Browser authentication request and response bodies."""

from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError("Enter a valid email address") from exc

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Display name cannot be blank")
        return value


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class LogoutRequest(StrictModel):
    pass


class MembershipSummary(StrictModel):
    org_id: UUID
    name: str
    roles: list[str]


class RegisterResponse(StrictModel):
    user_id: UUID
    email: str
    display_name: str


class LoginResponse(StrictModel):
    user_id: UUID
    memberships: list[MembershipSummary]


class MeResponse(LoginResponse):
    email: str
    display_name: str
    csrf_token: str
