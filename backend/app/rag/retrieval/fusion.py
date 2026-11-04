"""Bounded reciprocal-rank fusion over already scoped passage IDs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    passage_id: str
    score: float
    dense_rank: int | None
    lexical_rank: int | None


def fuse_rankings(
    dense_ids: Sequence[str], lexical_ids: Sequence[str], *,
    rrf_k: int = 60, per_list_limit: int = 20, fused_limit: int = 30,
) -> list[FusedCandidate]:
    """Deduplicate each input, fuse 1-based ranks, and cap returned candidates.

    The caller must apply organisation, approval, and effective-date scope before
    passing either list; fusion does not perform authorization or eligibility.
    """
    if rrf_k <= 0 or per_list_limit <= 0 or fused_limit <= 0:
        raise ValueError("RRF and candidate limits must be positive")
    ranks: dict[str, list[int | None]] = {}
    for list_index, candidate_ids in enumerate((dense_ids, lexical_ids)):
        seen: set[str] = set()
        for passage_id in candidate_ids:
            if not passage_id or not passage_id.strip():
                raise ValueError("candidate IDs must be nonempty")
            if passage_id in seen:
                continue
            seen.add(passage_id)
            if len(seen) > per_list_limit:
                break
            ranks.setdefault(passage_id, [None, None])[list_index] = len(seen)
    candidates = [
        FusedCandidate(
            passage_id=passage_id,
            score=sum(1.0 / (rrf_k + rank) for rank in pair if rank is not None),
            dense_rank=pair[0], lexical_rank=pair[1],
        )
        for passage_id, pair in ranks.items()
    ]
    candidates.sort(key=lambda item: (
        -item.score,
        min(rank for rank in (item.dense_rank, item.lexical_rank) if rank is not None),
        item.passage_id,
    ))
    return candidates[:fused_limit]
