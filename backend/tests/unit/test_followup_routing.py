"""Follow-up routing uses context only to choose retrieval, never as policy."""

import pytest

from app.rag.retrieval.followup import asks_about_review_coverage, route_followup


@pytest.mark.parametrize("question", [
    "How it is insufficient",
    "How is it insufficient?",
    "Why is evidence coverage insufficient?",
    "Why was the review partial?",
])
def test_review_coverage_question_identified_without_recasting_unrelated_topics(question):
    assert asks_about_review_coverage(question)
    assert not asks_about_review_coverage("What is the warranty period?")


def test_direct_finding_uses_only_rechecked_current_evidence():
    assert route_followup(
        "Why was that claim flagged?", has_finding=True,
        current_finding_evidence=True, has_recent_context=True,
    ).route == "reuse_evidence"
    assert route_followup(
        "Why was that claim flagged?", has_finding=True,
        current_finding_evidence=False, has_recent_context=True,
    ).route == "rewrite_retrieve"


def test_contextual_and_new_topic_routes():
    assert route_followup(
        "What about Sunday?", has_finding=False,
        current_finding_evidence=False, has_recent_context=True,
    ).route == "rewrite_retrieve"
    assert route_followup(
        "What is the approved warranty period?", has_finding=False,
        current_finding_evidence=False, has_recent_context=False,
    ).route == "fresh_retrieve"
    assert route_followup(
        "Why?", has_finding=False,
        current_finding_evidence=False, has_recent_context=False,
    ).route == "clarify"


@pytest.mark.parametrize("question", [
    "Explain the approved warranty period.",
    "What source confirms the refund policy?",
    "What evidence supports the Sunday opening hours?",
    "Why is the caffeine content limited?",
    "Is the refund suggestion allowed?",
])
@pytest.mark.parametrize("has_recent_context", [False, True])
def test_new_topic_searches_even_when_selected_finding_has_current_evidence(
    question, has_recent_context,
):
    assert route_followup(
        question, has_finding=True, current_finding_evidence=True,
        has_recent_context=has_recent_context,
    ).route == "fresh_retrieve"


@pytest.mark.parametrize("question", [
    "What about Sunday hours?",
    "Explain this warranty policy.",
    "What is its caffeine content?",
    "What source supports that refund policy?",
    "Explain this finding and the warranty period.",
    "What evidence supports this finding about refunds?",
    "Why was that claim flagged? Also explain Sunday hours.",
])
def test_contextual_topics_do_not_reuse_selected_finding_citations(question):
    assert route_followup(
        question, has_finding=True, current_finding_evidence=True,
        has_recent_context=True,
    ).route == "rewrite_retrieve"


@pytest.mark.parametrize("question", [
    "Why was that claim flagged?",
    "Explain this finding.",
    "Please explain the selected finding!",
    "What evidence supports this finding?",
    "Which source supports the current finding?",
    "What is the evidence for this finding?",
    "Show me the citations for the suggested edit.",
    "  EXPLAIN   this finding?  ",
])
def test_explicit_finding_question_may_reuse_only_current_citations(question):
    assert route_followup(
        question, has_finding=True, current_finding_evidence=True,
        has_recent_context=False,
    ).route == "reuse_evidence"
    assert route_followup(
        question, has_finding=True, current_finding_evidence=False,
        has_recent_context=False,
    ).route == "rewrite_retrieve"


@pytest.mark.parametrize("question", ["Why?", "How?", "What about that?"])
def test_short_reference_uses_context_to_search_without_assuming_evidence_support(question):
    assert route_followup(
        question, has_finding=True, current_finding_evidence=True,
        has_recent_context=False,
    ).route == "rewrite_retrieve"
    assert route_followup(
        question, has_finding=False, current_finding_evidence=False,
        has_recent_context=False,
    ).route == "clarify"


@pytest.mark.parametrize("question", [None, "", " \t\n"])
def test_empty_question_is_rejected(question):
    with pytest.raises(ValueError, match="question is required"):
        route_followup(
            question, has_finding=True, current_finding_evidence=True,
            has_recent_context=True,
        )
