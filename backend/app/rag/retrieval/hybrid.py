"""Tenant-scoped hybrid retrieval over currently approved source passages."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from rank_bm25 import BM25Plus
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.rag.embeddings import MAX_INPUT_TOKENS, QUERY_PREFIX
from app.rag.retrieval.fusion import FusedCandidate, fuse_rankings

MAX_QUERY_CHARACTERS = 4000
MAX_QUERY_TOKENS = MAX_INPUT_TOKENS


_ELIGIBLE = """
    FROM source_passages AS p
    JOIN source_versions AS v ON v.id = p.version_id AND v.org_id = p.org_id
    JOIN sources AS s ON s.id = p.source_id AND s.org_id = p.org_id
    WHERE p.org_id = :org_id
      AND v.approval_status = 'approved' AND v.ingestion_status = 'ready'
      AND s.archived_at IS NULL
      AND v.effective_from <= :as_of
      AND (v.effective_to IS NULL OR :as_of < v.effective_to)
      AND (CAST(:category AS text) IS NULL OR p.category = CAST(:category AS text))
      AND p.embedding_model = :model_id
"""


class QueryEmbedder(Protocol):
    model_id: str

    def count_tokens(self, full_input: str) -> int: ...

    def embed_query(self, query: str) -> list[float]: ...


class CorpusChanged(RuntimeError):
    """The approved corpus changed while selecting evidence; retry selection."""


@dataclass(frozen=True, slots=True)
class EvidencePassage:
    passage_id: UUID
    source_id: UUID
    version_id: UUID
    source_ref: str
    source_version: str
    title: str
    text: str
    category: str
    effective_from: date
    effective_to: date | None
    fusion: FusedCandidate


def normalize_search_query(value: str) -> str:
    """Normalize only search input; source text and citation anchors stay exact."""
    if not isinstance(value, str):
        raise ValueError("query must be a string")
    return unicodedata.normalize("NFC", " ".join(value.split()))


def query_within_bounds(value: str, embedder: QueryEmbedder) -> bool:
    """Check normalized query text against character and full E5 token budgets."""
    return (
        1 <= len(value) <= MAX_QUERY_CHARACTERS
        and embedder.count_tokens(QUERY_PREFIX + value) <= MAX_QUERY_TOKENS
    )


def tokenize(text_value: str) -> list[str]:
    """Use identical Unicode lexical tokenization for guidance and queries."""
    words: list[str] = []
    current: list[str] = []
    for character in unicodedata.normalize("NFC", text_value.casefold()):
        if character == "_" or unicodedata.category(character)[0] in {"L", "N", "M"}:
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def _corpus_version(db: Session, org_id: UUID) -> int:
    version = db.execute(text("""
        SELECT corpus_version FROM organisations WHERE id = :org_id
    """), {"org_id": org_id}).scalar_one_or_none()
    if version is None:
        raise ValueError("organisation does not exist")
    return int(version)


def _vector_literal(values: list[float]) -> str:
    if len(values) != 384:
        raise ValueError("query embedding must have 384 dimensions")
    import math

    if not all(math.isfinite(value) for value in values):
        raise ValueError("query embedding must contain finite values")
    return "[" + ",".join(format(value, ".10g") for value in values) + "]"


def retrieve_evidence(
    db: Session,
    *,
    org_id: UUID,
    as_of: date,
    query: str,
    category: str | None,
    corpus_version: int,
    embedder: QueryEmbedder,
    top_k: int = 8,
) -> list[EvidencePassage]:
    """Fuse exact scoped cosine and scoped BM25; reject a changed corpus.

    The caller derives ``org_id`` from authenticated membership and controls the
    transaction. A changed version raises so the caller can restart selection and
    save one coherent evidence snapshot before sending it to the LLM.
    """
    query = normalize_search_query(query)
    if not query_within_bounds(query, embedder):
        raise ValueError("query must contain 1 to 4000 characters and at most 512 model tokens")
    if top_k < 1 or top_k > 8:
        raise ValueError("top_k must be between 1 and 8")
    if corpus_version < 0:
        raise ValueError("corpus_version must be nonnegative")
    if _corpus_version(db, org_id) != corpus_version:
        raise CorpusChanged("approved corpus changed before retrieval")

    embedding = _vector_literal(embedder.embed_query(query))
    params = {
        "org_id": org_id, "as_of": as_of, "category": category,
        "model_id": embedder.model_id, "embedding": embedding, "limit": 20,
    }
    dense_rows = db.execute(text("""
        SELECT p.id
    """ + _ELIGIBLE + """
        ORDER BY p.embedding <=> CAST(:embedding AS vector), p.id
        LIMIT :limit
    """), params).mappings().all()
    # Lexical candidates are obtained with the same eligibility predicate, never
    # by filtering a global cross-tenant BM25 ranking after scoring.
    lexical_rows = db.execute(text("""
        SELECT p.id, p.source_id, p.version_id, p.source_ref,
               v.version_label AS source_version, p.title,
               p.text_content AS text, p.category,
               v.effective_from, v.effective_to
    """ + _ELIGIBLE + " ORDER BY p.id"), params).mappings().all()
    if _corpus_version(db, org_id) != corpus_version:
        raise CorpusChanged("approved corpus changed during retrieval")
    if not lexical_rows:
        return []

    tokens = [tokenize(row["text"]) for row in lexical_rows]
    lexical_query = tokenize(query)
    if lexical_query and any(tokens):
        scores = BM25Plus(tokens).get_scores(lexical_query)
        query_terms = set(lexical_query)
        ranked = sorted(
            range(len(lexical_rows)),
            key=lambda index: (-float(scores[index]), str(lexical_rows[index]["id"])),
        )
        lexical_ids = [
            str(lexical_rows[index]["id"]) for index in ranked
            if query_terms.intersection(tokens[index])
        ][:20]
    else:
        lexical_ids = []
    fused = fuse_rankings(
        [str(row["id"]) for row in dense_rows], lexical_ids,
        per_list_limit=20, fused_limit=30,
    )[:top_k]
    by_id = {str(row["id"]): row for row in lexical_rows}
    output: list[EvidencePassage] = []
    for candidate in fused:
        row = by_id.get(candidate.passage_id)
        if row is None:
            # This would indicate a mismatched eligibility query or concurrent
            # source mutation; do not serve ambiguous evidence.
            raise CorpusChanged("retrieval candidates changed during selection")
        output.append(EvidencePassage(
            passage_id=row["id"], source_id=row["source_id"],
            version_id=row["version_id"], source_ref=row["source_ref"],
            source_version=row["source_version"], title=row["title"],
            text=row["text"], category=row["category"],
            effective_from=row["effective_from"], effective_to=row["effective_to"],
            fusion=candidate,
        ))
    return output
