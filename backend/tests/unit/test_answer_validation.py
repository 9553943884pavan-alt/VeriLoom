"""Follow-up answers cite only exact current passages."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.rag.answer_validation import RewriteCandidate, validate_answer


PASSAGE = UUID("11111111-1111-4111-8111-111111111111")


def test_answer_requires_exact_current_quote():
    candidate = {
        "answer_status": "answered", "answer": "The approved policy says two years.",
        "evidence": [{"passage_id": str(PASSAGE), "supporting_quote": "two years"}],
    }
    output = validate_answer(candidate, current_passages={PASSAGE: "Warranty is two years."})
    assert output.passage_ids == (PASSAGE,)
    with pytest.raises(ValueError):
        validate_answer(candidate, current_passages={PASSAGE: "Warranty is one year."})
    with pytest.raises(ValueError):
        validate_answer(candidate, current_passages={})


def test_no_evidence_for_abstention_and_no_unapproved_fields():
    output = validate_answer({
        "answer_status": "insufficient_evidence", "answer": "No approved source establishes this.",
        "evidence": [],
    }, current_passages={})
    assert output.passage_ids == ()
    with pytest.raises(ValidationError):
        validate_answer({
            "answer_status": "answered", "answer": "Maybe", "evidence": [],
        }, current_passages={})


def test_rewrite_fields_are_mutually_exclusive():
    assert RewriteCandidate.model_validate({
        "needs_clarification": False, "standalone_query": "Nook Sunday hours",
        "clarification_question": None,
    }).standalone_query == "Nook Sunday hours"
    with pytest.raises(ValidationError):
        RewriteCandidate.model_validate({
            "needs_clarification": True, "standalone_query": "Nook Sunday hours",
            "clarification_question": "Which location?",
        })
