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


def test_compliant_preferred_term_is_not_a_finding_but_real_issue_remains():
    preferred = UUID("22222222-2222-4222-8222-222222222222")
    review = {
        "coverage": "partial", "coverage_reason": "The calorie claim needs verification.",
        "findings": [
            {
                "category": "terminology", "severity": "medium", "evidence_status": "supported",
                "quote": "Cardamom Chai", "explanation": "This already uses the correct preferred term; no change needed.",
                "suggested_edit": "Cardamom Chai",
                "evidence": [{"passage_id": str(preferred), "supporting_quote": "Preferred term: Cardamom Chai."}],
            },
            {
                "category": "factual_claim", "severity": "high", "evidence_status": "insufficient",
                "quote": "120 calories per cup", "explanation": "The calorie value is unverified.",
                "suggested_edit": "Ask for verified nutrition data before publishing.", "evidence": [],
            },
        ],
    }
    output = validate_review(
        review, revision_text="Cardamom Chai contains 120 calories per cup.",
        supplied_passages={preferred: "Preferred term: Cardamom Chai."}, quality_filter=True,
    )
    assert len(output.findings) == 1
    assert output.findings[0].candidate.category == "factual_claim"
    assert output.coverage == "partial"


def test_unchanged_suggestion_is_discarded_without_hiding_supported_issue():
    review = candidate()
    review["findings"][0]["suggested_edit"] = "best"
    output = validate_review(
        review, revision_text="best coffee", supplied_passages={PASSAGE: "Do not say best"},
        quality_filter=True,
    )
    assert len(output.findings) == 1
    assert output.findings[0].candidate.suggested_edit is None
    assert output.findings[0].anchor_start == 0


def test_unrelated_citation_is_dropped_and_never_becomes_a_clean_pass():
    review = candidate(quote="120 calories", supporting_quote="Preferred term: Cardamom Chai.")
    review["findings"][0]["category"] = "factual_claim"
    output = validate_review(
        review, revision_text="Cardamom Chai has 120 calories.",
        supplied_passages={PASSAGE: "Preferred term: Cardamom Chai."}, quality_filter=True,
    )
    assert output.findings == ()
    assert output.coverage == "partial"
    assert "unrelated guidance" in output.coverage_reason


def test_mixed_valid_and_unrelated_findings_keeps_valid_violation():
    review = candidate(quote="best", supporting_quote="Do not say best")
    review["findings"].append({
        "category": "tone", "severity": "low", "evidence_status": "supported",
        "quote": "coffee", "explanation": "Invented tone issue.", "suggested_edit": None,
        "evidence": [{"passage_id": str(PASSAGE),
                      "supporting_quote": "Preferred term: Cardamom Chai."}],
    })
    output = validate_review(review, revision_text="best coffee", supplied_passages={
        PASSAGE: "Do not say best. Preferred term: Cardamom Chai.",
    }, quality_filter=True)
    assert len(output.findings) == 1
    assert output.findings[0].candidate.quote == "best"
    assert output.coverage == "partial"


def test_worker_default_keeps_prior_structural_validation_until_holdout_gate():
    review = candidate(quote="best", supporting_quote="Do not say best")
    review["findings"][0]["suggested_edit"] = "best"
    output = validate_review(review, revision_text="best coffee", supplied_passages={
        PASSAGE: "Do not say best",
    })
    assert output.findings[0].candidate.suggested_edit == "best"


def test_optional_filter_keeps_late_unicode_codepoint_anchor_in_long_draft():
    prefix = "हिंदी café ☕ " * 800
    draft = prefix + "best coffee"
    output = validate_review(
        candidate(), revision_text=draft,
        supplied_passages={PASSAGE: "Do not say best without proof"},
        quality_filter=True,
    )
    assert len(output.findings) == 1
    assert output.findings[0].anchor_start == len(prefix)
    assert draft[output.findings[0].anchor_start:output.findings[0].anchor_end] == "best"
