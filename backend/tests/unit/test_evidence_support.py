"""Conservative evidence mismatch checks for model-generated findings."""

import pytest

from app.rag.evidence_support import reject_obviously_unrelated_evidence


@pytest.mark.parametrize("category", ["factual_claim", "promotional_claim", "tone"])
def test_bare_preferred_term_cannot_support_unrelated_category(category: str) -> None:
    with pytest.raises(ValueError, match="unrelated"):
        reject_obviously_unrelated_evidence(
            category=category,
            supporting_quotes=("Preferred term: Cardamom Chai.",),
        )


def test_direct_nutrition_gap_rule_remains_eligible() -> None:
    reject_obviously_unrelated_evidence(
        category="factual_claim",
        supporting_quotes=(
            "The current demo facts do not specify allergens, dietary certifications, nutritional values, or health benefits for any menu item.",
        ),
    )


def test_mixed_term_and_direct_rule_remains_eligible() -> None:
    reject_obviously_unrelated_evidence(
        category="factual_claim",
        supporting_quotes=(
            "Preferred term: Cardamom Chai.",
            "The current demo facts do not specify nutritional values for any menu item.",
        ),
    )


def test_preferred_term_can_support_terminology() -> None:
    reject_obviously_unrelated_evidence(
        category="terminology",
        supporting_quotes=("Preferred term: Cardamom Chai.",),
    )


def test_extended_rule_is_not_misclassified_as_bare_term() -> None:
    reject_obviously_unrelated_evidence(
        category="factual_claim",
        supporting_quotes=(
            "Preferred term: Cardamom Chai. Do not invent nutrition values for this item.",
        ),
    )
