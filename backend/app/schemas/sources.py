"""Request and response shapes for uploaded organisation guidance."""

from datetime import date
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
