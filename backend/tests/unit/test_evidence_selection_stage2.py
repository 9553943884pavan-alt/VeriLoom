"""Selection remains bounded, deterministic, and provenance-compatible."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.rag.retrieval.evidence_selection import select_interleaved


def _passage(value: int) -> SimpleNamespace:
    return SimpleNamespace(passage_id=UUID(int=value), source_ref=f"source-{value}")


def test_interleaving_matches_stage1_and_reserves_later_queries() -> None:
    first = [_passage(value) for value in range(1, 9)]
    second = [_passage(1), _passage(20), _passage(21)]
    selected = select_interleaved([first, second])
    assert [item.source_ref for item in selected] == [
        "source-1", "source-2", "source-20", "source-3",
        "source-21", "source-4", "source-5", "source-6",
    ]
    assert [item.source_ref for item in second] == ["source-1", "source-20", "source-21"]


def test_interleaving_deduplicates_identity_and_handles_empty_queries() -> None:
    selected = select_interleaved([[_passage(1), _passage(1)], [], [_passage(2)]])
    assert [item.passage_id for item in selected] == [UUID(int=1), UUID(int=2)]
    assert select_interleaved([[], []]) == []


@pytest.mark.parametrize("limit", [0, 9])
def test_interleaving_rejects_unbounded_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        select_interleaved([[_passage(1)]], limit=limit)
