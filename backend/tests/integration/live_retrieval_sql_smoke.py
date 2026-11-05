"""Opt-in read-only pgvector check using synthetic CTE relations.

Run from backend: python -m tests.integration.live_retrieval_sql_smoke
Default mode uses DATABASE_URL with pgvector already installed. Retrieval is
read-only over synthetic CTE relations, without application records/provider calls.
--disposable instead creates its own random database, installs vector only there,
and drops that exact database afterwards. The application schema is never changed.
This checks live SQL/math parity, not persisted ingestion or index performance.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from app.core.config import get_settings  # noqa: E402
from app.db.session import sqlalchemy_url  # noqa: E402
from app.rag.embeddings import get_embedder  # noqa: E402
from app.rag.retrieval.fusion import fuse_rankings  # noqa: E402
from app.rag.retrieval.hybrid import retrieve_evidence  # noqa: E402
from scripts.evaluate_hybrid_mvp import ORG, SyntheticSession  # noqa: E402
from scripts.evaluate_text_mvp import DATA_DIR, _read_jsonl, eligible_knowledge  # noqa: E402

CTES = """
WITH raw AS (
    SELECT * FROM jsonb_to_recordset(CAST(:audit_rows AS jsonb)) AS x(
        id uuid,source_id uuid,version_id uuid,org_id uuid,source_ref text,
        source_version text,title text,text_content text,category text,
        effective_from date,effective_to date,embedding text,embedding_model text)
), source_passages AS (
    SELECT id,source_id,version_id,org_id,source_ref,title,text_content,category,
           CAST(embedding AS vector) AS embedding,embedding_model FROM raw
), source_versions AS (
    SELECT DISTINCT version_id AS id,org_id,source_version AS version_label,
           effective_from,effective_to,'approved'::text AS approval_status,
           'ready'::text AS ingestion_status FROM raw
), sources AS (
    SELECT DISTINCT source_id AS id,org_id,NULL::timestamptz AS archived_at FROM raw
), organisations AS (
    SELECT CAST(:audit_org AS uuid) AS id,1::bigint AS corpus_version
)
"""


class VectorExtensionUnavailable(RuntimeError):
    """The selected database needs an approved pgvector migration first."""


class ReadOnlyFixtureSession:
    def __init__(self, connection, rows: list[dict]) -> None:
        self.connection = connection
        self.params = {"audit_rows": json.dumps(rows), "audit_org": str(ORG)}
        self.dense_calls = 0
        self.lexical_calls = 0

    def execute(self, statement, params):
        sql = str(statement)
        if not sql.lstrip().startswith("SELECT") or params.get("org_id") != ORG:
            raise ValueError("Only scoped synthetic retrieval SELECTs are allowed")
        if "ORDER BY p.embedding <=>" in sql:
            self.dense_calls += 1
        elif "SELECT p.id, p.source_id" in sql:
            self.lexical_calls += 1
        return self.connection.execute(text(CTES + sql), {**params, **self.params})


def run(database_url: str | None = None) -> dict:
    as_of = date(2026, 11, 5)
    records = eligible_knowledge(_read_jsonl(DATA_DIR / "knowledge/records.jsonl"), as_of)
    case = next(c for c in _read_jsonl(DATA_DIR / "evaluation/drafts.jsonl")
                if c["case_id"] == "NK-16" and c["split"] == "development")
    embedder = get_embedder()
    vectors = embedder.embed_passages(["passage: " + r["text"] for r in records])
    simulated = SyntheticSession(records, vectors, embedder.model_id, as_of)
    rows = [{
        "id": str(r["id"]), "source_id": str(r["source_id"]),
        "version_id": str(r["version_id"]), "org_id": str(ORG),
        "source_ref": r["source_ref"], "source_version": r["source_version"],
        "title": r["title"], "text_content": r["text"], "category": r["category"],
        "effective_from": r["effective_from"].isoformat(),
        "effective_to": r["effective_to"].isoformat() if r["effective_to"] else None,
        "embedding": json.dumps(r["vector"]), "embedding_model": embedder.model_id,
    } for r in simulated.rows]
    engine = create_engine(
        sqlalchemy_url(database_url or get_settings().DATABASE_URL.get_secret_value()),
        connect_args={"connect_timeout": 5}, hide_parameters=True,
    )
    traces: list[tuple[list[str], list[str]]] = []

    def capture(dense, lexical, **kwargs):
        traces.append((list(dense), list(lexical)))
        return fuse_rankings(dense, lexical, **kwargs)

    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            extension = connection.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            )).scalar_one_or_none()
            if extension is None:
                raise VectorExtensionUnavailable("No vector extension is installed")
            actual = ReadOnlyFixtureSession(connection, rows)
            args = dict(org_id=ORG, as_of=as_of, query=case["content"], category=None,
                        corpus_version=1, embedder=embedder, top_k=8)
            with patch("app.rag.retrieval.hybrid.fuse_rankings", side_effect=capture):
                live = retrieve_evidence(actual, **args)
                local = retrieve_evidence(simulated, **args)
            if len(traces) != 2 or traces[0] != traces[1]:
                raise AssertionError("Live and simulated candidate rankings differ")
            if [p.source_ref for p in live] != [p.source_ref for p in local]:
                raise AssertionError("Live and simulated final passages differ")
            return dict(
                case_id=case["case_id"], pgvector_version=extension,
                real_model=embedder.model_id, corpus_size=len(rows),
                dense_sql_calls=actual.dense_calls, lexical_sql_calls=actual.lexical_calls,
                dense_candidates=len(traces[0][0]), lexical_candidates=len(traces[0][1]),
                final_refs=[p.source_ref for p in live], live_local_rankings_equal=True,
                read_only=True, application_records_read=False, provider_calls=0,
                scope="Real pgvector and production SQL; synthetic CTE relations, no stored-vector/index test",
            )
    finally:
        engine.dispose()


def run_disposable() -> dict:
    """Create only our random test DB; drop it after all connections close."""
    configured = make_url(sqlalchemy_url(get_settings().DATABASE_URL.get_secret_value()))
    name = "veriloom_live_test_retrieval_" + uuid4().hex[:16]
    target = configured.set(database=name)
    admin = create_engine(configured.set(database="postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 5}, hide_parameters=True)
    fixture_engine = None
    created = False
    result = None
    try:
        with admin.connect() as connection:
            connection.connection.driver_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        print(json.dumps({"created_test_database": name}), flush=True)
        fixture_engine = create_engine(target, connect_args={"connect_timeout": 5}, hide_parameters=True)
        with fixture_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION vector"))
        result = run(target.render_as_string(hide_password=False))
    finally:
        if fixture_engine is not None:
            fixture_engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.connection.driver_connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                if connection.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}).first():
                    raise AssertionError("Disposable database cleanup could not be verified")
            print(json.dumps({"test_database_dropped": True, "absence_confirmed": True}), flush=True)
        admin.dispose()
    assert result is not None
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable", action="store_true",
                        help="Create a separate random test database with pgvector, then drop it")
    args = parser.parse_args()
    try:
        print(json.dumps(run_disposable() if args.disposable else run(), indent=2))
    except Exception as exc:
        # Never dump a connection URL, query parameters or provider credentials.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
