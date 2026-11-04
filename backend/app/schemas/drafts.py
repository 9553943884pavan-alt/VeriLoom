"""Public schemas for editable drafts and immutable submitted revisions."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

Text = Annotated[str, StringConstraints(min_length=1, max_length=20000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class DraftCreate(BaseModel):
    title: ShortText
    channel: ShortText
    content_format: Literal["text"] = "text"
    content: Text


class DraftPatch(BaseModel):
    expected_working_copy_version: int = Field(ge=1)
    title: ShortText | None = None
    channel: ShortText | None = None
    content: Text | None = None


class DraftSubmit(BaseModel):
    expected_working_copy_version: int = Field(ge=1)
    reviewer_user_id: UUID


class DecisionRequest(BaseModel):
    expected_revision_id: UUID
    expected_review_version: int = Field(ge=1)
    action: Literal["request_changes", "approve"]
    comment: Annotated[str, StringConstraints(max_length=4000)] | None = None
    override_reason: Annotated[str, StringConstraints(max_length=4000)] | None = None


class CommentRequest(BaseModel):
    revision_id: UUID
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DraftOut(BaseModel):
    model_config = ConfigDict(extra="allow")


class DraftList(BaseModel):
    items: list[dict]
    next_cursor: str | None


class SubmissionAccepted(BaseModel):
    revision_id: UUID
    revision_number: int
    review_id: UUID
    draft_state: str
    review_state: str
    status_url: str
