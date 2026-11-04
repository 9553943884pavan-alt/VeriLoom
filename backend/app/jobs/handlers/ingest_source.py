"""Bounded ingestion of one pending, private text guidance version."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.integrations.r2 import R2Storage
from app.rag.chunking import chunk_text
from app.rag.ingestion import (
    GuidanceTooLarge,
    InvalidGuidanceText,
    UnsupportedGuidanceFormat,
    extract_guidance,
)

MAX_PASSAGES = 5000
MAX_TOKENS = 512


class PassageEmbedder(Protocol):
    """Local E5 adapter; inputs include the ``passage: `` prefix."""

    model_id: str

    def count_tokens(self, full_input: str) -> int: ...

    def embed_passages(self, full_inputs: list[str]) -> Sequence[Sequence[float]]: ...


class IngestionFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def ingest_source(
    db: Session,
    *,
    org_id: UUID,
    version_id: UUID,
    storage: R2Storage,
    embedder: PassageEmbedder,
) -> None:
    """Extract, chunk, embed, and atomically publish one source version."""
    row = db.execute(text("""
        SELECT v.id, v.source_id, v.r2_object_key, v.sha256, v.byte_size, v.mime_type,
               v.ingestion_status, s.title, s.category
        FROM source_versions v JOIN sources s
          ON s.org_id = v.org_id AND s.id = v.source_id
        WHERE v.org_id = :org_id AND v.id = :version_id
    """), {"org_id": org_id, "version_id": version_id}).mappings().first()
    if row is None:
        raise IngestionFailure("source_version_not_found")
    row = dict(row)
    if row["ingestion_status"] == "ready":
        return
    if row["ingestion_status"] not in {"queued", "processing"}:
        raise IngestionFailure("source_version_not_ingestable")
    if not row["r2_object_key"]:
        raise IngestionFailure("source_object_missing")

    db.execute(text("""
        UPDATE source_versions SET ingestion_status = 'processing', ingestion_error_code = NULL
        WHERE org_id = :org_id AND id = :version_id AND ingestion_status IN ('queued','processing')
    """), {"org_id": org_id, "version_id": version_id})
    db.commit()
    try:
        original = storage.get_bytes(row["r2_object_key"])
        extension = {"text/plain": ".txt", "text/markdown": ".md"}.get(row["mime_type"])
        if extension is None:
            raise IngestionFailure("unsupported_source_type")
        extracted = extract_guidance("source" + extension, original)
        if hashlib.sha256(original).hexdigest() != row["sha256"] or len(original) != row["byte_size"]:
            raise IngestionFailure("source_integrity_mismatch")
        pieces = chunk_text(
            extracted.text, count_tokens=embedder.count_tokens, max_tokens=MAX_TOKENS,
            default_title=row["title"],
        )
        if len(pieces) > MAX_PASSAGES:
            raise IngestionFailure("too_many_passages")
        inputs = ["passage: " + piece.text for piece in pieces]
        vectors = embedder.embed_passages(inputs)
        if len(vectors) != len(pieces) or any(
            len(vector) != 384 or any(not math.isfinite(float(value)) for value in vector)
            for vector in vectors
        ):
            raise IngestionFailure("invalid_embedding_output", retryable=True)
    except (GuidanceTooLarge, InvalidGuidanceText, UnsupportedGuidanceFormat, ValueError) as exc:
        raise IngestionFailure("invalid_source_text") from exc
    except IngestionFailure:
        raise
    except Exception as exc:
        raise IngestionFailure("ingestion_dependency_error", retryable=True) from exc

    try:
        for piece, vector in zip(pieces, vectors, strict=True):
            source_ref = f"DOC-{row['source_id']}#P{piece.chunk_index + 1:04d}"
            vector_literal = "[" + ",".join(str(float(value)) for value in vector) + "]"
            db.execute(text("""
                INSERT INTO source_passages (
                    org_id, source_id, version_id, source_ref, chunk_index, title,
                    text_content, start_offset, end_offset, category, embedding, embedding_model
                ) VALUES (
                    :org_id, :source_id, :version_id, :source_ref, :chunk_index, :title,
                    :text_content, :start_offset, :end_offset, :category,
                    CAST(:embedding AS vector), :embedding_model
                )
            """), {
                "org_id": org_id, "source_id": row["source_id"], "version_id": version_id,
                "source_ref": source_ref, "chunk_index": piece.chunk_index, "title": piece.title,
                "text_content": piece.text, "start_offset": piece.start_offset,
                "end_offset": piece.end_offset, "category": row["category"],
                "embedding": vector_literal, "embedding_model": embedder.model_id,
            })
        db.execute(text("""
            UPDATE source_versions SET ingestion_status = 'ready', ingestion_error_code = NULL,
                ready_at = now()
            WHERE org_id = :org_id AND id = :version_id AND ingestion_status = 'processing'
        """), {"org_id": org_id, "version_id": version_id})
        db.commit()
    except Exception as exc:
        db.rollback()
        raise IngestionFailure("passage_persistence_error", retryable=True) from exc


def build_ingestion_dependencies() -> tuple[R2Storage, PassageEmbedder]:
    """Construct adapters lazily so API imports do not load the ML stack."""
    from app.core.config import get_settings
    from app.rag.embeddings import MODEL_NAME, get_embedder

    configured_model = get_settings().EMBEDDING_MODEL
    if configured_model != MODEL_NAME:
        raise RuntimeError("The configured embedding model does not match the pinned E5 adapter.")
    return R2Storage(), get_embedder()
