"""Hybrid retrieval must scope both ranks before evidence reaches a model."""

from datetime import date
from uuid import UUID

import pytest

from app.rag.retrieval.hybrid import CorpusChanged, retrieve_evidence, tokenize


ORG = UUID("11111111-1111-4111-8111-111111111111")


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Db:
    def __init__(self, rows, versions=(3, 3)):
        self.rows = rows
        self.versions = iter(versions)
        self.queries = []

    def execute(self, statement, params):
        sql = str(statement)
        self.queries.append((sql, params))
        if "SELECT corpus_version" in sql:
            return Result([next(self.versions)])
        if "SELECT p.id\n" in sql:
            return Result([{"id": row["id"]} for row in reversed(self.rows)])
        return Result(self.rows)


class Embedder:
    model_id = "intfloat/multilingual-e5-small@test"

    def embed_query(self, query):
        assert query == "Nook 2-year warranty"
        return [0.01] * 384


def row(index, text_value):
    return {
        "id": UUID(int=index), "source_id": UUID(int=index + 100),
        "version_id": UUID(int=index + 200), "source_ref": f"REF-{index}",
        "source_version": "1.0", "title": f"Rule {index}",
        "text": text_value, "category": "facts",
        "effective_from": date(2026, 1, 1), "effective_to": None,
    }


def test_hybrid_filters_both_queries_and_returns_metadata():
    db = Db([row(1, "Nook warranty lasts 2 years"), row(2, "Brand tone is warm")])
    result = retrieve_evidence(
        db, org_id=ORG, as_of=date(2026, 11, 4), query="Nook 2-year warranty",
        category="facts", corpus_version=3, embedder=Embedder(),
    )
    assert len(result) == 2
    assert result[0].source_ref == "REF-1"
    assert result[0].fusion.lexical_rank == 1
    for sql, params in db.queries[1:3]:
        assert "p.org_id = :org_id" in sql
        assert "v.approval_status = 'approved'" in sql
        assert "v.ingestion_status = 'ready'" in sql
        assert "s.archived_at IS NULL" in sql
        assert "v.effective_from <= :as_of" in sql
        assert "p.category = CAST(:category AS text)" in sql
        assert "p.embedding_model = :model_id" in sql
        assert params["org_id"] == ORG


def test_change_in_corpus_aborts_evidence():
    db = Db([row(1, "Nook warranty")], versions=(3, 4))
    with pytest.raises(CorpusChanged):
        retrieve_evidence(
            db, org_id=ORG, as_of=date(2026, 11, 4), query="Nook 2-year warranty",
            category=None, corpus_version=3, embedder=Embedder(),
        )


def test_empty_scope_and_unicode_tokens():
    db = Db([], versions=(0, 0))
    assert retrieve_evidence(
        db, org_id=ORG, as_of=date(2026, 11, 4), query="Nook 2-year warranty",
        category=None, corpus_version=0, embedder=Embedder(),
    ) == []
    assert tokenize("नमस्ते Nook-2 Warranty") == ["नमस्ते", "nook", "2", "warranty"]
