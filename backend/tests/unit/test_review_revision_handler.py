from __future__ import annotations

from datetime import date
from uuid import UUID

from app.jobs.handlers.review_revision import ReviewFailure, review_revision
from app.jobs.handlers.review_revision import _queries
from app.rag.retrieval.fusion import FusedCandidate
from app.rag.retrieval.hybrid import EvidencePassage

ORG = UUID("11111111-1111-4111-8111-111111111111")
REVIEW = UUID("22222222-2222-4222-8222-222222222222")
REVISION = UUID("33333333-3333-4333-8333-333333333333")
PASSAGE = UUID("44444444-4444-4444-8444-444444444444")
SOURCE = UUID("55555555-5555-4555-8555-555555555555")
VERSION = UUID("66666666-6666-4666-8666-666666666666")


class Result:
    def __init__(self, row=None, scalar=None):
        self.row, self.scalar = row, scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


class FakeDB:
    def __init__(self):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.next_finding = UUID("77777777-7777-4777-8777-777777777777")

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT r.id AS review_id" in sql and "FOR UPDATE OF r" in sql:
            return Result({"review_id": REVIEW, "revision_id": REVISION, "state": "queued",
                           "reviewed_against_corpus_version": None, "evidence_as_of_date": None,
                           "content_nfc": "Try our best coffee", "channel": "social_post",
                           "actual_revision_id": REVISION, "corpus_version": 7,
                           "timezone": "Asia/Calcutta"})
        if "SELECT corpus_version FROM organisations" in sql:
            return Result(scalar=7)
        if "SELECT r.state,r.revision_id,d.current_revision_id" in sql:
            return Result({"state": "processing", "revision_id": REVISION,
                           "current_revision_id": REVISION})
        if "INSERT INTO findings" in sql:
            return Result(scalar=self.next_finding)
        return Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Embedder:
    model_id = "offline-e5"

    def count_tokens(self, full_input):
        return len(full_input)

    def embed_query(self, query):
        return [0.0] * 384


class Chat:
    model = "offline-model"

    def __init__(self, db):
        self.db = db
        self.calls = 0

    def complete_json(self, messages, *, schema_name, schema, max_tokens):
        self.calls += 1
        assert self.db.commits == 1  # snapshot transaction committed before provider call
        user = messages[-1]["content"]
        assert '"revision_id"' in user and '"channel": "social_post"' in user
        assert schema_name == "veriloom_initial_review_v2"
        assert "initial_review/2.0.0" not in user
        return {"coverage": "partial", "coverage_reason": "Other claims may need more evidence.",
                "findings": [{"category": "promotional_claim", "severity": "high",
                              "evidence_status": "supported", "quote": "best",
                              "explanation": "The supplied rule prohibits this claim.",
                              "suggested_edit": "coffee", "evidence": [
                                  {"passage_id": str(PASSAGE), "supporting_quote": "Do not say best."}]}]}


def evidence():
    return [EvidencePassage(PASSAGE, SOURCE, VERSION, "SRC#P1", "1.0", "Voice",
                            "Do not say best.", "guidelines", date(2026, 1, 1), None,
                            FusedCandidate(str(PASSAGE), 0.03, 1, 1))]


def test_review_snapshots_evidence_before_sarvam_and_persists_validated_citation(monkeypatch):
    import app.jobs.handlers.review_revision as handler

    db = FakeDB()
    chat = Chat(db)
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: evidence())
    review_revision(db, org_id=ORG, review_id=REVIEW, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))

    snapshot = next((sql, params) for sql, params in db.calls if "INSERT INTO review_evidence" in sql)
    assert snapshot[1]["supplied_text"] == "Do not say best."
    assert snapshot[1]["source_version"] == "1.0"
    assert chat.calls == 1
    assert any("o.time_zone AS timezone" in sql for sql, _ in db.calls)
    finding = next(params for sql, params in db.calls if "INSERT INTO findings" in sql)
    assert (finding["anchor_start"], finding["anchor_end"]) == (8, 12)
    citation = next(params for sql, params in db.calls if "INSERT INTO finding_citations" in sql)
    assert citation["excerpt"] == "Do not say best."
    assert citation["source_ref"] == "SRC#P1"
    assert any("state='ready_for_review'" in sql for sql, _ in db.calls)
    assert any("state='ready_for_review'" in sql and "UPDATE drafts" in sql for sql, _ in db.calls)


def test_no_evidence_finishes_as_insufficient_without_provider(monkeypatch):
    import app.jobs.handlers.review_revision as handler

    db = FakeDB()
    chat = Chat(db)
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: [])
    review_revision(db, org_id=ORG, review_id=REVIEW, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))
    assert chat.calls == 0
    update = next(params for sql, params in db.calls if "coverage=:coverage" in sql)
    assert update["coverage"] == "insufficient"
    assert "human must check" in update["coverage_reason"]


def test_inexact_model_citation_is_rejected_before_publication(monkeypatch):
    import app.jobs.handlers.review_revision as handler

    db = FakeDB()
    chat = Chat(db)
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: evidence())

    def malformed(*args, **kwargs):
        return {"coverage": "sufficient", "coverage_reason": None, "findings": [{
            "category": "tone", "severity": "low", "evidence_status": "supported", "quote": "best",
            "explanation": "Bad citation.", "suggested_edit": None,
            "evidence": [{"passage_id": str(PASSAGE), "supporting_quote": "not in the passage"}],
        }]}

    chat.complete_json = malformed
    try:
        review_revision(db, org_id=ORG, review_id=REVIEW, embedder=Embedder(), chat=chat,
                        today=date(2026, 11, 4))
    except ReviewFailure as exc:
        assert exc.code == "review_validation_error"
    else:
        raise AssertionError("expected the exact quote validator to reject the response")
    assert not any("INSERT INTO findings" in sql for sql, _ in db.calls)


def test_query_planner_obeys_e5_token_bound_and_flags_omitted_segments():
    text = " ".join(f"claim{i} " + ("x" * 450) + "." for i in range(14))
    queries, truncated = _queries(text, Embedder())
    assert truncated is True
    assert len(queries) == 8
    assert all(Embedder().count_tokens("query: " + query) <= 512 for query in queries)
    assert "claim13" not in " ".join(queries)
