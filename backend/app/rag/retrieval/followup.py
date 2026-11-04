"""Deterministic first-pass routing for reviewer follow-up questions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


Route = Literal["reuse_evidence", "rewrite_retrieve", "fresh_retrieve", "clarify"]
_DEICTIC = re.compile(r"\b(it|its|this|that|those|these|there|they|them|he|she)\b", re.I)
_DIRECT = re.compile(r"\b(why|explain|evidence|source|citation|flagged|suggestion)\b", re.I)
_REFERENCE_ONLY = re.compile(r"^(why|how|what about (that|it)|and (that|it))\??$", re.I)


@dataclass(frozen=True, slots=True)
class FollowupRoute:
    route: Route
    reason: str


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
    normalized = question.strip()
    if _REFERENCE_ONLY.fullmatch(normalized) and not (has_finding or has_recent_context):
        return FollowupRoute("clarify", "The question has no clear referent.")
    if has_finding and current_finding_evidence and _DIRECT.search(normalized):
        return FollowupRoute("reuse_evidence", "The current finding evidence may answer this direct question.")
    if _DEICTIC.search(normalized) or (has_recent_context and normalized.lower().startswith("what about")):
        if has_finding or has_recent_context:
            return FollowupRoute("rewrite_retrieve", "Context is needed for a standalone search query.")
        return FollowupRoute("clarify", "The reference is ambiguous without context.")
    return FollowupRoute("fresh_retrieve", "The question can be searched directly in current guidance.")
