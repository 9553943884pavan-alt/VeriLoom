"""Optional, bounded purpose queries for checkable nutrition and policy claims.

This module only constructs search inputs. It does not alter the immutable draft,
choose passages, interpret missing evidence, or make a review decision.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from app.rag.retrieval.hybrid import normalize_search_query, query_within_bounds

QUERY_PLANNER_VERSION = "purpose_queries/1.0.0"


class QueryTokenCounter(Protocol):
    def count_tokens(self, full_input: str) -> int: ...


# These are subject cues, not source identifiers or approved facts. They request
# applicable guidance; the search result must still be checked for actual support.
_NUTRITION_CUE = re.compile(
    r"\b(?:caffeine|calor(?:ie|ies|ic)|nutrition(?:al)?|allergens?|"
    r"protein|sugar[-\s]?free|vegan|gluten[-\s]?free)\b", re.IGNORECASE,
)
_POLICY_CUE = re.compile(r"\b(?:refunds?|returns?|warrant(?:y|ies))\b", re.IGNORECASE)
_PROMOTION_CUE = re.compile(
    r"\b(?:promotions?|discounts?|coupons?|promo\s+codes?|code\s+[a-z0-9]{3,})\b"
    r"|\b\d{1,3}\s?%\s?off\b", re.IGNORECASE,
)

_PURPOSE_QUERIES = (
    (_NUTRITION_CUE,
     "What approved nutritional values, dietary details, and caffeine information are available for menu items?"),
    (_POLICY_CUE,
     "What approved guidance applies when a factual claim has no authorised supporting evidence?"),
    (_PROMOTION_CUE,
     "What promotional offers, discount codes, and deadlines are currently approved?"),
)


def augment_queries(
    base_queries: Sequence[str], embedder: QueryTokenCounter, *, max_queries: int = 8,
) -> list[str]:
    """Append at most one search-only query for each detected claim subject.

    Base queries retain their order so all previously planned draft segments are
    searched. Optional purpose queries use only input text cues, never labels or
    source IDs. They consume spare query slots and pass the same E5/character
    limits as direct hybrid retrieval. The caller keeps its original truncation
    flag and source-offset bookkeeping.
    """
    if not 1 <= max_queries <= 8:
        raise ValueError("max_queries must be between 1 and 8")
    if not base_queries or len(base_queries) > max_queries:
        raise ValueError("base_queries must fit within max_queries")
    result = [normalize_search_query(query) for query in base_queries]
    if any(not query_within_bounds(query, embedder) for query in result):
        raise ValueError("base query exceeds search bounds")
    seen = set(result)
    subject_text = " ".join(result)
    for cue, purpose in _PURPOSE_QUERIES:
        if len(result) == max_queries:
            break
        if cue.search(subject_text) and purpose not in seen:
            if not query_within_bounds(purpose, embedder):
                raise ValueError("purpose query exceeds search bounds")
            result.append(purpose)
            seen.add(purpose)
    return result
