"""Hybrid retrieval must scope both ranks before evidence reaches a model."""

from datetime import date
from uuid import UUID

import pytest

from app.rag.retrieval.hybrid import (
    CorpusChanged,
    normalize_search_query,
    retrieve_evidence,
    tokenize,
)


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

    def count_tokens(self, full_input):
        return len(full_input.split())

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


class RecordingEmbedder(Embedder):
    def __init__(self, token_count=None):
        self.token_count = token_count
        self.counted = []
        self.embedded = []

    def count_tokens(self, full_input):
        self.counted.append(full_input)
        return self.token_count if self.token_count is not None else super().count_tokens(full_input)

    def embed_query(self, query):
        self.embedded.append(query)
        return [0.01] * 384


@pytest.mark.parametrize("query", [
    " " * 4100 + "Nook 2-year warranty",
    "\tNook" + "\u2003" * 4100 + "2-year\n warranty \r\n",
])
def test_query_whitespace_is_normalized_before_both_bounds(query):
    embedder = RecordingEmbedder()
    db = Db([row(1, "Nook warranty lasts 2 years")])
    retrieve_evidence(
        db, org_id=ORG, as_of=date(2026, 11, 4), query=query,
        category=None, corpus_version=3, embedder=embedder,
    )
    assert embedder.counted == ["query: Nook 2-year warranty"]
    assert embedder.embedded == ["Nook 2-year warranty"]


@pytest.mark.parametrize(("query", "token_count", "accepted"), [
    ("x" * 4000, 512, True),
    ("x" * 4001, 512, False),
    ("x", 512, True),
    ("x", 513, False),
    ("word " * 511, None, True),
    ("word " * 512, None, False),
    (" \t\n\u2003", None, False),
    (None, None, False),
])
def test_retrieval_query_bounds_precede_database_and_embedding(query, token_count, accepted):
    embedder = RecordingEmbedder(token_count)
    db = Db([])
    arguments = dict(
        org_id=ORG, as_of=date(2026, 11, 4), query=query,
        category=None, corpus_version=3, embedder=embedder,
    )
    if accepted:
        assert retrieve_evidence(db, **arguments) == []
        assert embedder.embedded == [normalize_search_query(query)]
    else:
        with pytest.raises(ValueError, match="query must"):
            retrieve_evidence(db, **arguments)
        assert db.queries == []
        assert embedder.embedded == []


@pytest.mark.parametrize(("source", "query"), [
    ("Café guidance", "Cafe\u0301"),
    ("Cafe\u0301 guidance", "CAFÉ"),
])
def test_canonically_equivalent_unicode_matches_without_changing_source(source, query):
    embedder = RecordingEmbedder()
    db = Db([row(1, source), row(2, "Unrelated hours")])
    result = retrieve_evidence(
        db, org_id=ORG, as_of=date(2026, 11, 4), query=query,
        category=None, corpus_version=3, embedder=embedder,
    )
    assert result[0].passage_id == UUID(int=1)
    assert result[0].fusion.lexical_rank == 1
    assert result[0].text == source
    assert tokenize(source)[0] == tokenize(query)[0] == "café"
    assert embedder.embedded == [normalize_search_query(query)]
