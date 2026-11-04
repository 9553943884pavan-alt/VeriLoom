"""Validate model review candidates against the immutable draft and prompt evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passage_id: UUID
    supporting_quote: str = Field(min_length=1, max_length=300)


class CandidateFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["tone", "terminology", "factual_claim", "promotional_claim", "accessibility", "other"]
    severity: Literal["low", "medium", "high"]
    evidence_status: Literal["supported", "insufficient"]
    quote: str | None = None
    explanation: str = Field(min_length=1, max_length=500)
    suggested_edit: str | None = Field(default=None, max_length=300)
    evidence: list[EvidenceClaim] = Field(max_length=3)

    @model_validator(mode="after")
    def check_evidence(self) -> "CandidateFinding":
        if self.evidence_status == "supported" and not self.evidence:
            raise ValueError("supported finding requires evidence")
        if self.evidence_status == "insufficient" and self.evidence:
            raise ValueError("insufficient finding cannot cite evidence")
        if self.quote is not None and not self.quote:
            raise ValueError("draft quote cannot be empty")
        if len({item.passage_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence passage")
        return self


class CandidateReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage: Literal["sufficient", "partial", "insufficient"]
    coverage_reason: str | None = Field(default=None, max_length=300)
    findings: list[CandidateFinding] = Field(max_length=12)

    @model_validator(mode="after")
    def check_coverage(self) -> "CandidateReview":
        if self.coverage == "sufficient" and self.coverage_reason is not None:
            raise ValueError("sufficient coverage must have no reason")
        if self.coverage != "sufficient" and not (self.coverage_reason or "").strip():
            raise ValueError("partial or insufficient coverage needs a reason")
        return self


@dataclass(frozen=True, slots=True)
class ValidatedFinding:
    candidate: CandidateFinding
    anchor_start: int | None
    anchor_end: int | None


@dataclass(frozen=True, slots=True)
class ValidatedReview:
    coverage: Literal["sufficient", "partial", "insufficient"]
    coverage_reason: str | None
    findings: tuple[ValidatedFinding, ...]


def validate_review(
    payload: object,
    *,
    revision_text: str,
    supplied_passages: dict[UUID, str],
) -> ValidatedReview:
    """Reject fabricated evidence and compute code-point anchors without guessing.

    This proves exact provenance, not semantic entailment. Human review remains
    necessary for whether a quoted rule actually supports a model finding.
    """
    candidate = CandidateReview.model_validate(payload)
    validated: list[ValidatedFinding] = []
    for finding in candidate.findings:
        for claim in finding.evidence:
            passage = supplied_passages.get(claim.passage_id)
            if passage is None or claim.supporting_quote not in passage:
                raise ValueError("finding cites absent or inexact supplied evidence")
        quote = finding.quote
        if quote is not None and quote not in revision_text:
            raise ValueError("finding quote is not in immutable revision")
        if quote is None or revision_text.count(quote) != 1:
            start = end = None
        else:
            start = revision_text.index(quote)
            end = start + len(quote)
            if revision_text[start:end] != quote:
                raise ValueError("finding anchor mismatch")
        validated.append(ValidatedFinding(finding, start, end))

    coverage, reason = candidate.coverage, candidate.coverage_reason
    if not supplied_passages:
        coverage = "insufficient"
        reason = "No approved guidance was available for this review; a human must check the draft."
    return ValidatedReview(coverage, reason, tuple(validated))
