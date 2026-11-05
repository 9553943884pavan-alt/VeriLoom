"""Deterministic boundaries for opt-in claim-purpose search queries."""

from __future__ import annotations

import pytest

from app.jobs.handlers.review_revision import _queries
from app.rag.retrieval.query_planning import augment_queries


class TokenCounter:
    def count_tokens(self, full_input: str) -> int:
        return len(full_input.split()) + 2


def test_nutrition_claim_adds_one_bounded_purpose_query() -> None:
    base = ["How many milligrams of caffeine are in Masala Chai?"]
    planned = augment_queries(base, TokenCounter())
    assert planned[0] == base[0]
    assert len(planned) == 2
    assert "nutritional values" in planned[1]
    assert "Masala Chai" not in planned[1]
    assert base == ["How many milligrams of caffeine are in Masala Chai?"]


def test_refund_claim_adds_generic_evidence_gap_query() -> None:
    planned = augment_queries(["Takeaway refunds are available within 30 days."], TokenCounter())
    assert len(planned) == 2
    assert "authorised supporting evidence" in planned[1]
    assert not any("NK-" in item for item in planned)


def test_promotion_claim_asks_for_approval_without_using_specific_code() -> None:
    planned = augment_queries(["Use code CHAI50 for 50% off until tonight."], TokenCounter())
    assert len(planned) == 2
    assert "discount codes" in planned[1]
    assert "CHAI50" not in planned[1]


@pytest.mark.parametrize("text", [
    "Cardamom Chai is ₹120 per cup.",
    "Take a quiet chai break at Nook & Kettle.",
    "Try Cardamom Chai.",
])
def test_unrelated_clean_claims_keep_original_search_count(text: str) -> None:
    assert augment_queries([text], TokenCounter()) == [text]


def test_all_existing_long_draft_segments_remain_first_and_bounded() -> None:
    base = [f"Segment {i} has caffeine." for i in range(8)]
    assert augment_queries(base, TokenCounter()) == base
    seven = base[:7]
    planned = augment_queries(seven, TokenCounter())
    assert planned[:7] == seven
    assert len(planned) == 8


def test_real_long_draft_planner_truncation_stays_truthful() -> None:
    draft = "x. " * 6000 + "Contains caffeine."
    base, truncated = _queries(draft, TokenCounter())
    assert truncated is True
    assert len(base) == 8
    assert augment_queries(base, TokenCounter()) == base


def test_multiple_topics_never_exceed_eight_and_do_not_duplicate_query() -> None:
    planned = augment_queries(["Caffeine refunds?", "Caffeine refunds?"], TokenCounter())
    assert planned[:2] == ["Caffeine refunds?", "Caffeine refunds?"]
    assert len(planned) == 4
    assert len(set(planned[2:])) == 2


def test_unicode_whitespace_is_search_only_normalized() -> None:
    original = "  Caf\u0065\u0301\u00a0refunds\n  are available.  "
    planned = augment_queries([original], TokenCounter())
    assert planned[0] == "Café refunds are available."
    assert original.startswith("  Cafe\u0301")
    assert len(planned) == 2


def test_same_character_and_token_bounds_as_hybrid_search() -> None:
    assert augment_queries([" " * 4100 + "refunds"], TokenCounter())[0] == "refunds"
    with pytest.raises(ValueError, match="search bounds"):
        augment_queries(["x" * 4001], TokenCounter())


def test_invalid_budget_cannot_discard_existing_planned_segments() -> None:
    with pytest.raises(ValueError, match="max_queries"):
        augment_queries(["a"], TokenCounter(), max_queries=9)
    with pytest.raises(ValueError, match="base_queries"):
        augment_queries(["one", "two"], TokenCounter(), max_queries=1)
