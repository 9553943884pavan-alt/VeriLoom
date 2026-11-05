"""Quality smoke judgments must catch the reproduced false positive/abstention."""

import json

import pytest

from scripts.evaluate_quality_regression import APPROVED, CASES, PASSAGE_ID, assess, messages_for, run


def test_compliant_claim_rejects_false_positive_and_allows_empty_findings():
    case = CASES[0]
    raw = {"coverage": "sufficient", "coverage_reason": None, "findings": []}
    assert assess(case, raw)["passed"]
    raw["findings"].append({
        "category": "promotional_claim", "severity": "high", "evidence_status": "supported",
        "quote": case.draft, "explanation": "This repeats an approved claim.", "suggested_edit": None,
        "evidence": [{"passage_id": str(PASSAGE_ID), "supporting_quote": APPROVED}],
    })
    assert not assess(case, raw)["passed"]


def test_approved_answer_requires_answered_status_and_the_actual_approval_quote():
    case = next(c for c in CASES if c.case_id == "approved_wording")
    assert not assess(case, {"answer_status": "insufficient_evidence", "answer": "Cannot verify.",
                             "evidence": []})["passed"]
    raw = {"answer_status": "answered", "answer": "The guidance explicitly approves that sentence.",
           "evidence": [{"passage_id": str(PASSAGE_ID), "supporting_quote": APPROVED}]}
    assert assess(case, raw)["passed"]
    raw["evidence"][0]["supporting_quote"] = "Use a clear, friendly tone."
    assert not assess(case, raw)["passed"]
    raw["evidence"][0]["supporting_quote"] = "fabricated quote"
    with pytest.raises(ValueError):
        assess(case, raw)


def test_labels_are_not_sent_to_provider_and_prior_chat_is_separate_from_evidence():
    for case in CASES:
        payload = json.loads(messages_for(case)[-1]["content"])
        assert not {"expected_findings", "evidence_status", "answer_status", "supporting_quote", "issue"} & payload.keys()
    unknown = next(c for c in CASES if c.case_id == "unknown_warranty")
    payload = json.loads(messages_for(unknown)[-1]["content"])
    assert "lifetime warranty" in payload["recent_turns"][0]["answer"]
    assert "warranty" not in payload["current_evidence"][0]["text"]


def test_errors_are_failures_and_do_not_leak_provider_content():
    class FailingChat:
        def complete_json(self, *args, **kwargs):
            raise ValueError("secret provider response")

    result = run(FailingChat(), cases=CASES[:1])
    assert not result["all_passed"]
    assert result["cases"][0]["error_type"] == "ValueError"
    assert "secret provider response" not in json.dumps(result)
    with pytest.raises(ValueError):
        run(FailingChat(), repeats=4)


def test_contextual_case_runs_rewrite_and_answers_using_its_resolved_wording():
    case = next(c for c in CASES if c.case_id == "contextual_approval")

    class Chat:
        model = "synthetic-model"

        def complete_json(self, messages, *, schema_name, **kwargs):
            if schema_name == "veriloom_rewrite_followup_v2":
                return {"needs_clarification": False,
                        "standalone_query": "Is 'Our coffee is freshly roasted.' approved?",
                        "clarification_question": None}
            payload = json.loads(messages[-1]["content"])
            assert payload["rewritten_query"] == "Is 'Our coffee is freshly roasted.' approved?"
            return {"answer_status": "answered", "answer": "That exact sentence is listed as approved.",
                    "evidence": [{"passage_id": str(PASSAGE_ID), "supporting_quote": APPROVED}]}

    result = run(Chat(), cases=(case,))
    assert result["all_passed"]
    assert result["logical_calls"] == 2
    assert result["cases"][0]["route"] == "rewrite_retrieve"


def test_retrieval_failure_prevents_any_provider_transfer():
    class NoChat:
        def complete_json(self, *args, **kwargs):
            raise AssertionError("No provider call allowed")

    def no_synthetic_evidence(query):
        raise ValueError("Wrong corpus")

    result = run(NoChat(), cases=CASES[:1], retrieve=no_synthetic_evidence)
    assert not result["all_passed"]
    assert result["logical_calls"] == 0


def test_real_passage_ids_are_validated_without_rewriting_source_ids():
    from uuid import UUID

    actual = UUID(int=123)
    case = next(c for c in CASES if c.case_id == "approved_wording")
    raw = {"answer_status": "answered", "answer": "The source lists the sentence as approved.",
           "evidence": [{"passage_id": str(actual), "supporting_quote": APPROVED}]}
    assert assess(case, raw, passages={actual: case.guidance})["passed"]
    with pytest.raises(ValueError):
        assess(case, raw)
