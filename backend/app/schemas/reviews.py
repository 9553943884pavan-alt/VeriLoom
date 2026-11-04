"""Public review, comment and decision response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from pydantic import StringConstraints
from typing import Annotated, Literal


class ReviewDecisionSummary(BaseModel):
    decision_id: UUID
    action: Literal["request_changes", "approve"]
    comment: str | None
    override_reason: str | None
    decided_by_user_id: UUID
    decided_at: datetime


class ReviewOut(BaseModel):
    review_id: UUID
    draft_id: UUID
    revision_id: UUID
    revision_number: int
    assigned_reviewer_user_id: UUID
    state: str
    coverage: str | None
    coverage_reason: str | None
    review_version: int
    reviewed_against_corpus_version: int | None
    findings: list[dict]
    failure: dict | None
    created_at: datetime
    completed_at: datetime | None
    decision: ReviewDecisionSummary | None = None


class DecisionOut(BaseModel):
    decision_id: UUID
    review_id: UUID
    revision_id: UUID
    action: str
    comment: str | None
    override_reason: str | None
    decided_by_user_id: UUID
    decided_at: datetime
    review_state: str
    draft_state: str
    review_version: int


class CommentOut(BaseModel):
    comment_id: UUID
    revision_id: UUID
    body: str
    actor_user_id: UUID
    created_at: datetime


class QuestionRequest(BaseModel):
    revision_id: UUID
    finding_id: UUID | None = None
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class QuestionAccepted(BaseModel):
    turn_id: UUID
    state: str
    status_url: str


class QuestionOut(BaseModel):
    turn_id: UUID
    revision_id: UUID
    finding_id: UUID | None
    question: str
    rewritten_query: str | None
    state: str
    route: str | None
    answer_status: str | None
    answer: str | None
    citations: list[dict]
    created_at: datetime
    answered_at: datetime | None


class RetryAccepted(BaseModel):
    review_id: UUID
    state: str
    status_url: str
