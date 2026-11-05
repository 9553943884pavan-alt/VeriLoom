"""Narrow, deterministic rejection of plainly unrelated finding citations.

This is not an entailment checker. Other semantic support still needs human review.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_BARE_PREFERRED_TERM = re.compile(r"\s*Preferred term:\s*[^.;\n]+\.\s*", re.IGNORECASE)


def reject_obviously_unrelated_evidence(*, category: str, supporting_quotes: Sequence[str]) -> None:
    """A bare preferred-name declaration cannot justify a different issue type."""
    if category != "terminology" and supporting_quotes and all(
        _BARE_PREFERRED_TERM.fullmatch(quote) for quote in supporting_quotes
    ):
        raise ValueError("finding cites unrelated terminology-only evidence")
