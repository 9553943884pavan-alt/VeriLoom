"""Validate follow-up rewrites and answers against current supplied evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RewriteCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needs_clarification: bool
    standalone_query: str | None = Field(default=None, max_length=300)
    clarification_question: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def check_exclusive(self) -> "RewriteCandidate":
        if self.needs_clarification:
            if self.standalone_query is not None or not (self.clarification_question or "").strip():
                raise ValueError("clarification needs a question and no search query")
        elif self.clarification_question is not None or not (self.standalone_query or "").strip():
            raise ValueError("rewrite needs a standalone query and no clarification")
        return self


class AnswerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passage_id: UUID
    supporting_quote: str = Field(min_length=1, max_length=300)


class AnswerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_status: Literal["answered", "clarification_needed", "insufficient_evidence"]
    answer: str = Field(min_length=1, max_length=900)
    evidence: list[AnswerEvidence] = Field(max_length=3)

    @model_validator(mode="after")
    def check_evidence(self) -> "AnswerCandidate":
        if self.answer_status == "answered" and not self.evidence:
            raise ValueError("answered response requires current evidence")
        if self.answer_status != "answered" and self.evidence:
            raise ValueError("non-answer must not cite evidence")
        if len({item.passage_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate answer evidence")
        return self


@dataclass(frozen=True, slots=True)
class ValidatedAnswer:
    candidate: AnswerCandidate
    passage_ids: tuple[UUID, ...]


def validate_answer(payload: object, *, current_passages: dict[UUID, str]) -> ValidatedAnswer:
    """Reject fabricated or inexact citations before persisting a chat turn."""
    candidate = AnswerCandidate.model_validate(payload)
    for claim in candidate.evidence:
        text = current_passages.get(claim.passage_id)
        if text is None or claim.supporting_quote not in text:
            raise ValueError("answer cites absent or inexact current evidence")
    return ValidatedAnswer(candidate, tuple(item.passage_id for item in candidate.evidence))
