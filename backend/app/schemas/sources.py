"""Request and response shapes for uploaded organisation guidance."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.auth import StrictModel


SourceCategory = Literal["guidelines", "facts", "terminology", "messaging"]


class SourceUploadMetadata(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    category: SourceCategory
    effective_from: date
    effective_to: date | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be blank")
        return value

    @model_validator(mode="after")
    def valid_effective_dates(self) -> "SourceUploadMetadata":
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be later than effective_from")
        return self


class SourceUploadAccepted(StrictModel):
    source_id: UUID
    version_id: UUID
    ingestion_status: Literal["queued"]


class SourceApprovalRequest(StrictModel):
    expected_corpus_version: int = Field(ge=0)


class SourceApprovalResponse(StrictModel):
    source_id: UUID
    approved_version_id: UUID
    corpus_version: int = Field(ge=1)


class SourceArchiveResponse(StrictModel):
    source_id: UUID
    corpus_version: int = Field(ge=1)


class SourceSummary(StrictModel):
    source_id: UUID
    title: str
    category: SourceCategory
    archived_at: datetime | None
    current_status: Literal["active", "archived", "expired", "pending"]
    active_version_id: UUID | None
    active_version_label: str | None
    latest_version_id: UUID | None
    latest_version_label: str | None
    ingestion_status: Literal["queued", "processing", "ready", "failed"] | None
    approval_status: Literal["pending", "approved", "archived"] | None
    ingestion_error_code: str | None


class SourceList(StrictModel):
    items: list[SourceSummary]
    next_cursor: str | None


class SourceVersionDetail(StrictModel):
    source_id: UUID
    version_id: UUID
    title: str
    category: SourceCategory
    version_label: str
    effective_from: date
    effective_to: date | None
    ingestion_status: Literal["queued", "processing", "ready", "failed"]
    approval_status: Literal["pending", "approved", "archived"]
    ingestion_error_code: str | None
    passage_count: int
    original_available: bool
