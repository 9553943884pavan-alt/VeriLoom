"""Follow-up routing uses context only to choose retrieval, never as policy."""

from app.rag.retrieval.followup import route_followup


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
