from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

from app.jobs.handlers.ingest_source import IngestionFailure, ingest_source

ORG = UUID("11111111-1111-4111-8111-111111111111")
SOURCE = UUID("22222222-2222-4222-8222-222222222222")
VERSION = UUID("33333333-3333-4333-8333-333333333333")
CONTENT = b"# Claims\nUse clear wording.\n"


class Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class DB:
    def __init__(self, row):
        self.row = row
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        if "SELECT v.id" in str(statement):
            return Result(self.row)
        return Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Storage:
    def get_bytes(self, key):
        assert key.endswith("/original")
        return CONTENT


class Embedder:
    model_id = "test-e5@rev"

    def count_tokens(self, full_input):
        assert full_input.startswith("passage: ")
        return len(full_input)

    def embed_passages(self, full_inputs):
        self.inputs = full_inputs
        return [[0.1] * 384 for _ in full_inputs]


def make_db(status="queued"):
    return DB({"id": VERSION, "source_id": SOURCE,
               "r2_object_key": f"org/{ORG}/sources/{SOURCE}/versions/{VERSION}/original",
               "sha256": hashlib.sha256(CONTENT).hexdigest(), "byte_size": len(CONTENT),
               "mime_type": "text/markdown",
               "ingestion_status": status, "title": "Brand guidance", "category": "guidelines"})


def test_handler_persists_exact_passage_and_readiness_atomically():
    db, embedder = make_db(), Embedder()
    ingest_source(db, org_id=ORG, version_id=VERSION, storage=Storage(), embedder=embedder)
    inserts = [(sql, params) for sql, params in db.calls if "INSERT INTO source_passages" in sql]
    select_sql = db.calls[0][0]
    assert "v.title, v.category" in select_sql
    assert "s.title" not in select_sql and "s.category" not in select_sql
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "CAST(:embedding AS vector)" in sql
    assert params["source_ref"] == f"DOC-{SOURCE}#P0001"
    assert params["text_content"] == "Use clear wording."
    assert CONTENT.decode()[params["start_offset"]:params["end_offset"]] == params["text_content"]
    assert params["embedding_model"] == "test-e5@rev"
    assert embedder.inputs == ["passage: Use clear wording."]
    assert any("ingestion_status = 'ready'" in call[0] for call in db.calls)
    assert db.commits == 2 and db.rollbacks == 0


def test_handler_is_idempotent_for_ready_version():
    db = make_db("ready")
    ingest_source(db, org_id=ORG, version_id=VERSION, storage=Storage(), embedder=Embedder())
    assert len(db.calls) == 1


def test_handler_rejects_hash_mismatch_without_readying_version():
    db = make_db()
    db.row["sha256"] = "0" * 64
    with pytest.raises(IngestionFailure) as exc:
        ingest_source(db, org_id=ORG, version_id=VERSION, storage=Storage(), embedder=Embedder())
    assert exc.value.code == "source_integrity_mismatch"
    assert not any("ingestion_status = 'ready'" in call[0] for call in db.calls)


def test_handler_rejects_tenant_mismatch_as_not_found():
    db = DB(None)
    with pytest.raises(IngestionFailure) as exc:
        ingest_source(db, org_id=ORG, version_id=VERSION, storage=Storage(), embedder=Embedder())
    assert exc.value.code == "source_version_not_found"
