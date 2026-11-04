"""Model JSON cannot establish citations or draft anchors by assertion alone."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.rag.review_validation import validate_review


PASSAGE = UUID("11111111-1111-4111-8111-111111111111")


def candidate(quote="best", supporting_quote="Do not say best"):
    return {
        "coverage": "sufficient", "coverage_reason": None,
        "findings": [{
            "category": "promotional_claim", "severity": "high",
            "evidence_status": "supported", "quote": quote,
            "explanation": "The approved rule prohibits best claims.",
            "suggested_edit": "", "evidence": [{
                "passage_id": str(PASSAGE), "supporting_quote": supporting_quote,
            }],
        }],
    }


def test_exact_citation_and_unicode_codepoint_anchor():
    output = validate_review(candidate(), revision_text="☕ best coffee", supplied_passages={
        PASSAGE: "Do not say best without proof",
    })
    assert output.findings[0].anchor_start == 2
    assert output.findings[0].anchor_end == 6
    assert output.coverage == "sufficient"


def test_duplicate_quote_has_no_guessed_anchor():
    output = validate_review(candidate(), revision_text="best best", supplied_passages={
        PASSAGE: "Do not say best without proof",
    })
    assert output.findings[0].anchor_start is None


def test_forged_or_inexact_evidence_rejected():
    with pytest.raises(ValueError):
        validate_review(candidate(), revision_text="best", supplied_passages={})
    with pytest.raises(ValueError):
        validate_review(candidate(supporting_quote="Never best"), revision_text="best", supplied_passages={
            PASSAGE: "Do not say best without proof",
        })


def test_no_evidence_downgrades_coverage():
    output = validate_review(
        {"coverage": "sufficient", "coverage_reason": None, "findings": []},
        revision_text="hello", supplied_passages={},
    )
    assert output.coverage == "insufficient"
    assert output.coverage_reason


def test_extra_fields_and_visual_category_rejected():
    bad = candidate()
    bad["findings"][0]["category"] = "visual_asset"
    with pytest.raises(ValidationError):
        validate_review(bad, revision_text="best", supplied_passages={PASSAGE: "Do not say best"})
    bad = candidate()
    bad["secret"] = "ignore"
    with pytest.raises(ValidationError):
        validate_review(bad, revision_text="best", supplied_passages={PASSAGE: "Do not say best"})
