"""Opt-in real E5/Markdown ingestion and scoped retrieval in a disposable DB.

Run from backend: python -B -m tests.integration.live_stage2_ingestion_retrieval
Uses the upload repository and production ingestion handler, with an in-memory
original-object store. Does not exercise HTTP, R2 networking, workers or providers.
Never migrates or writes application data in the configured database.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories.sources import insert_source_upload
from app.db.session import sqlalchemy_url
from app.jobs.handlers.ingest_source import ingest_source
from app.rag.embeddings import get_embedder
from app.rag.ingestion import extract_guidance
from app.rag.retrieval.hybrid import retrieve_evidence

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
CONTENT = "# Café guidance\r\nCafe\u0301 prices are approved at 120 rupees.\r\n\r\n# हिन्दी\r\nनमस्ते। स्पष्ट भाषा का प्रयोग करें।\r\n".encode()


class OriginalStorage:
    """Object I/O double; extraction/chunking/embedding/persistence remain real."""

    def __init__(self) -> None:
        self.originals: dict[str, bytes] = {}

    def get_bytes(self, key: str) -> bytes:
        return self.originals[key]


def exercise(engine, embedder) -> dict:
    day = date(2026, 11, 5)
    org, other, actor = (uuid4() for _ in range(3))
    extracted = extract_guidance("guidance.md", CONTENT)
    storage = OriginalStorage()
    fixtures = {}
    with Session(engine) as db:
        db.execute(text("INSERT INTO users(id,email,display_name,password_hash) "
                        "VALUES (:id,:email,'Stage2 synthetic','unused')"),
                   {"id": actor, "email": f"{actor}@example.com"})
        for oid in (org, other):
            db.execute(text("INSERT INTO organisations(id,slug,name,time_zone) "
                            "VALUES (:id,:slug,'Stage2 synthetic','UTC')"),
                       {"id": oid, "slug": str(oid)})
        db.commit()
        for label in ("active", "pending", "future", "expired", "archived", "other", "not_ready", "wrong_model"):
            source, version = uuid4(), uuid4()
            tenant = other if label == "other" else org
            key = f"org/{tenant}/sources/{source}/versions/{version}/original"
            storage.originals[key] = CONTENT
            insert_source_upload(
                db, org_id=tenant, source_id=source, version_id=version, actor_id=actor,
                title="Synthetic Café guidance", category="guidelines", object_key=key,
                sha256=extracted.sha256, mime_type=extracted.mime_type, byte_size=len(CONTENT),
                effective_from=day + timedelta(days=1) if label == "future" else day - timedelta(days=2),
                effective_to=day if label == "expired" else None,
            )
            db.commit()
            ingest_source(db, org_id=tenant, version_id=version, storage=storage, embedder=embedder)
            rows = db.execute(text("SELECT text_content,start_offset,end_offset,embedding_model,"
                                   "vector_dims(embedding) AS dimensions FROM source_passages WHERE version_id=:id"),
                              {"id": version}).mappings().all()
            assert rows
            for row in rows:
                assert extracted.text[row["start_offset"]:row["end_offset"]] == row["text_content"]
                assert row["embedding_model"] == embedder.model_id and row["dimensions"] == 384
                assert embedder.count_tokens("passage: " + row["text_content"]) <= 512
            if label not in {"pending", "not_ready"}:
                db.execute(text("UPDATE source_versions SET approval_status='approved' WHERE id=:id"), {"id": version})
            if label == "archived":
                db.execute(text("UPDATE sources SET archived_at=now() WHERE id=:id"), {"id": source})
            if label == "not_ready":
                db.execute(text("UPDATE source_versions SET ingestion_status='processing' WHERE id=:id"), {"id": version})
            if label == "wrong_model":
                db.execute(text("UPDATE source_passages SET embedding_model='different-model' WHERE version_id=:id"), {"id": version})
            fixtures[label] = {"source": source, "version": version, "passages": len(rows)}
            db.commit()
        corpus = db.execute(text("SELECT corpus_version FROM organisations WHERE id=:id"), {"id": org}).scalar_one()
        observations = []
        for query in ("Cafe\u0301 prices", "नमस्ते स्पष्ट भाषा"):
            passages = retrieve_evidence(db, org_id=org, as_of=day, query=query, category=None,
                                         corpus_version=corpus, embedder=embedder)
            assert passages and all(p.source_id == fixtures["active"]["source"] for p in passages)
            observations.append({"query": query, "returned": len(passages),
                                 "lexical_matches": sum(p.fusion.lexical_rank is not None for p in passages)})
            assert observations[-1]["lexical_matches"] > 0
        # Empty category and independent tenant must preserve the eligibility scope.
        assert not retrieve_evidence(db, org_id=org, as_of=day, query="Café", category="facts",
                                     corpus_version=corpus, embedder=embedder)
        other_corpus = db.execute(text("SELECT corpus_version FROM organisations WHERE id=:id"), {"id": other}).scalar_one()
        other_passages = retrieve_evidence(db, org_id=other, as_of=day, query="Café", category=None,
                                           corpus_version=other_corpus, embedder=embedder)
        assert other_passages and all(p.source_id == fixtures["other"]["source"] for p in other_passages)
    return {"model": embedder.model_id, "original_bytes": len(CONTENT),
            "stored_passages": sum(f["passages"] for f in fixtures.values()),
            "checks": list(fixtures), "queries": observations, "exact_spans": True,
            "category_isolation": True, "tenant_isolation": True, "provider_calls": 0,
            "boundary": "Upload repository + ingestion handler + stored pgvector + production SQL; in-memory object storage; no HTTP/R2/worker."}


def run_disposable() -> dict:
    embedder = get_embedder()
    configured = make_url(sqlalchemy_url(get_settings().DATABASE_URL.get_secret_value()))
    name = "veriloom_live_test_stage2_" + uuid4().hex[:16]
    assert name != configured.database and name.startswith("veriloom_live_test_stage2_")
    target = configured.set(database=name)
    admin = create_engine(configured.set(database="postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 10}, hide_parameters=True)
    engine = None
    created = False
    previous = os.environ.get("DATABASE_URL")
    result = None
    try:
        with admin.connect() as connection:
            connection.connection.driver_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        print(json.dumps({"created_disposable_database": name}), flush=True)
        os.environ["DATABASE_URL"] = target.render_as_string(hide_password=False)
        backend = Path(__file__).resolve().parents[2]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "alembic"))
        command.upgrade(config, "head")
        engine = create_engine(target, connect_args={"connect_timeout": 10}, hide_parameters=True)
        result = exercise(engine, embedder)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.connection.driver_connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                assert not connection.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}).first()
            print(json.dumps({"disposable_database_dropped": True, "absence_confirmed": True}), flush=True)
        admin.dispose()
    assert result is not None
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(run_disposable(), indent=2, ensure_ascii=True))
    except Exception as exc:
        original = getattr(exc, "orig", None)
        diagnostics = getattr(original, "diag", None)
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "sqlstate": getattr(original, "sqlstate", None),
                          "constraint": getattr(diagnostics, "constraint_name", None),
                          "column": getattr(diagnostics, "column_name", None)}))
        raise SystemExit(1) from None
