"""Deterministic first-pass routing for reviewer follow-up questions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


Route = Literal["reuse_evidence", "rewrite_retrieve", "fresh_retrieve", "clarify"]
_DEICTIC = re.compile(r"\b(it|its|this|that|those|these|there|they|them|he|she)\b", re.I)
_FINDING_REFERENCE = (
    r"(?:this|that|the|the selected|the current|selected|current) "
    r"(?:finding|claim|suggestion|suggested edit|issue)"
)
# Match the whole request: a finding reference alone cannot establish that an
# additional topic is answerable from the selected finding's citations.
_DIRECT_FINDING = re.compile(
    rf"(?:why (?:was|is) {_FINDING_REFERENCE} (?:flagged|raised)"
    rf"|(?:please )?explain {_FINDING_REFERENCE}"
    rf"|(?:what|which) (?:evidence|sources?|citations?|rules?|guidance) "
    rf"(?:supports?|explains?) {_FINDING_REFERENCE}"
    rf"|(?:what|which) (?:is|are) the (?:evidence|sources?|citations?) "
    rf"(?:for|behind) {_FINDING_REFERENCE}"
    rf"|(?:show|give) me (?:the )?(?:evidence|sources?|citations?) "
    rf"for {_FINDING_REFERENCE})[?.!]*",
    re.I,
)
_FINDING_SOURCE_QUESTION = re.compile(
    rf"(?:what|which) (?:approved )?(?:sources?|citations?|evidence) "
    rf"supports? {_FINDING_REFERENCE}[?.!]*",
    re.I,
)
_REFERENCE_ONLY = re.compile(r"^(why|how|what about (that|it)|and (that|it))\??$", re.I)
_QUOTED_WORDING = re.compile(r"['\u201c][^'\u201d]{2,}['\u201d]")
_POLICY_CUE = re.compile(r"\b(guidance|policy|rule|approved|allowed|prohibited)\b", re.I)
_REVIEW_COVERAGE_QUESTION = re.compile(
    r"^(?:(?:why|how)\s+(?:(?:is|was)\s+)?(?:it|this|the review|the coverage|evidence coverage)\s+"
    r"(?:(?:is|was)\s+)?(?:insufficient|partial)"
    r"|(?:why|how)\s+(?:(?:is|was)\s+)?(?:review\s+)?(?:evidence\s+)?coverage\s+"
    r"(?:(?:is|was)\s+)?(?:insufficient|partial))[?.!]*$",
    re.I,
)


@dataclass(frozen=True, slots=True)
class FollowupRoute:
    route: Route
    reason: str


def asks_about_review_coverage(question: str) -> bool:
    """Identify narrow requests to explain the persisted review diagnostic.

    This never establishes a brand fact and must not be used as retrieval evidence.
    """
    return bool(_REVIEW_COVERAGE_QUESTION.fullmatch(" ".join(question.split())))


def asks_for_finding_source(question: str) -> bool:
    """Only match a direct request for the selected finding's current citation."""
    return bool(_FINDING_SOURCE_QUESTION.fullmatch(" ".join(question.split())))


def route_followup(
    question: str,
    *,
    has_finding: bool,
    current_finding_evidence: bool,
    has_recent_context: bool,
) -> FollowupRoute:
    """Choose retrieval path; the caller still checks current source eligibility.

    Never use old citations as current authority solely because they are stored.
    ``current_finding_evidence`` means the caller has rechecked approval and date.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question is required")
    normalized = " ".join(question.split())
    if _REFERENCE_ONLY.fullmatch(normalized) and not (has_finding or has_recent_context):
        return FollowupRoute("clarify", "The question has no clear referent.")
    if has_finding and current_finding_evidence and _DIRECT_FINDING.fullmatch(normalized):
        return FollowupRoute("reuse_evidence", "The current finding evidence may answer this direct question.")
    if _QUOTED_WORDING.search(normalized) and _POLICY_CUE.search(
        _QUOTED_WORDING.sub("", normalized)
    ):
        return FollowupRoute("fresh_retrieve", "The quoted wording and policy question are explicit.")
    if (_DEICTIC.search(normalized) or _REFERENCE_ONLY.fullmatch(normalized)
            or _DIRECT_FINDING.fullmatch(normalized)
            or (has_recent_context and normalized.lower().startswith("what about"))):
        if has_finding or has_recent_context:
            return FollowupRoute("rewrite_retrieve", "Context is needed for a standalone search query.")
        return FollowupRoute("clarify", "The reference is ambiguous without context.")
    return FollowupRoute("fresh_retrieve", "The question can be searched directly in current guidance.")
