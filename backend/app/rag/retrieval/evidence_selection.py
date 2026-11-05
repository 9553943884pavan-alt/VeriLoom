"""Bounded selection from already scoped, ranked retrieval results.

Selection is intentionally label-blind. Callers execute every planned search
before passing its results here and can build query-match provenance from the
unmodified per-query lists after selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.rag.retrieval.hybrid import EvidencePassage


def select_interleaved(
    results_by_query: Sequence[Sequence[EvidencePassage]], *, limit: int = 8,
) -> list[EvidencePassage]:
    """Select unique passage IDs by rank then query, preserving Stage 1 order.

    The helper does not rank, filter for tenant/approval/date, or infer support.
    It expects each input list to be ordered and scoped by ``retrieve_evidence``.
    """
    if not 1 <= limit <= 8:
        raise ValueError("limit must be between 1 and 8")
    selected: dict[UUID, EvidencePassage] = {}
    max_rank = max((len(results) for results in results_by_query), default=0)
    for result_index in range(max_rank):
        for results in results_by_query:
            if result_index < len(results):
                passage = results[result_index]
                selected.setdefault(passage.passage_id, passage)
            if len(selected) >= limit:
                return list(selected.values())
    return list(selected.values())
