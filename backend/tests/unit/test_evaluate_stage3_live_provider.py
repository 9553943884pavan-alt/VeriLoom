"""The live probe remains bounded and stores only synthetic aggregate metadata."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from scripts.evaluate_stage3_live_provider import CASES, PASSAGE_ID, assess, run, safe_error


def _finding(quote: str, *, edit: str | None = None) -> dict:
    return {
        "category": "promotional_claim", "severity": "high",
        "evidence_status": "supported", "quote": quote,
        "explanation": "The invented rule prohibits this claim.",
        "suggested_edit": edit,
        "evidence": [{"passage_id": str(PASSAGE_ID),
                      "supporting_quote": "Do not claim guaranteed savings"}],
    }


def test_replays_identical_candidate_and_clears_no_op_edit() -> None:
    case = CASES[0]
    payload = {"coverage": "sufficient", "coverage_reason": None,
               "findings": [_finding("guarantees savings", edit="guarantees savings")]}
    baseline = assess(case, payload, quality_filter=False)
    stage3 = assess(case, payload, quality_filter=True)
    assert baseline["true_positive"] == stage3["true_positive"] == 1
    assert baseline["exact_anchors"] == stage3["exact_anchors"] == 1
    assert baseline["no_op_edits"] == 1
    assert stage3["no_op_edits"] == 0


def test_validation_error_exposes_only_field_path() -> None:
    class SecretModel(BaseModel):
        field: int

    try:
        SecretModel.model_validate({"field": "SECRET_CONTENT"})
    except ValidationError as exc:
        metadata = safe_error(exc)
    else:
        raise AssertionError("Expected validation failure")
    assert metadata == {"type": "ValidationError", "fields": ["field"]}
    assert "SECRET_CONTENT" not in json.dumps(metadata)


def test_run_caps_calls_and_does_not_store_raw_response() -> None:
    class FakeChat:
        model = "synthetic-model"
        last_provider = "sarvam"
        last_model = "synthetic-model"
        calls = 0

        def complete_json(self, messages, *, schema_name, schema, max_tokens):
            self.calls += 1
            return {"coverage": "sufficient", "coverage_reason": None,
                    "findings": [_finding("guarantees savings")]}

    chat = FakeChat()
    result = run(chat, CASES * 2)
    assert chat.calls == 3
    assert len(result["cases"]) == 3
    assert result["cases"][0]["provider"] == "sarvam"
    assert "guarantees savings" not in json.dumps(result)
