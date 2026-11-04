from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from app.jobs.handlers.answer_question import AnswerFailure, _preserves_terms, answer_question
from app.rag.retrieval.fusion import FusedCandidate
from app.rag.retrieval.hybrid import EvidencePassage

ORG = UUID("11111111-1111-4111-8111-111111111111")
TURN = UUID("22222222-2222-4222-8222-222222222222")
REVIEW = UUID("33333333-3333-4333-8333-333333333333")
REVISION = UUID("44444444-4444-4444-8444-444444444444")
PASSAGE = UUID("55555555-5555-4555-8555-555555555555")
SOURCE = UUID("66666666-6666-4666-8666-666666666666")
VERSION = UUID("77777777-7777-4777-8777-777777777777")


class Result:
    def __init__(self, row=None, rows=None, scalar=None):
        self.row, self.rows, self.scalar = row, rows or [], scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar


class FakeDB:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT t.id,t.review_id" in sql:
            return Result(row={"id": TURN, "review_id": REVIEW, "finding_id": None,
                               "question": "May we say best?", "state": "queued",
                               "created_at": datetime.now(timezone.utc),
                               "revision_id": REVISION, "review_state": "ready_for_review",
                               "current_revision_id": REVISION,
                               "content_nfc": "Our coffee is best.", "channel": "social_post",
                               "corpus_version": 3, "timezone": "Asia/Calcutta"})
        if "SELECT question,answer FROM chat_turns" in sql:
            return Result(rows=[])
        if "SELECT corpus_version FROM organisations" in sql:
            return Result(scalar=3)
        if "SELECT t.state,r.revision_id" in sql:
            return Result(row={"state": "processing", "revision_id": REVISION,
                               "current_revision_id": REVISION})
        return Result()

    def commit(self):
        self.commits += 1


class Embedder:
    model_id = "offline-e5"

    def count_tokens(self, full_input):
        return len(full_input)

    def embed_query(self, query):
        return [0.0] * 384


class Chat:
    model = "offline-model"

    def __init__(self, db, *, quote="Do not say best."):
        self.db = db
        self.calls = 0
        self.quote = quote

    def complete_json(self, messages, *, schema_name, schema, max_tokens):
        assert self.db.commits == 1  # Evidence was committed before external transfer.
        assert schema_name == "veriloom_grounded_answer_v2"
        assert '"current_evidence"' in messages[-1]["content"]
        self.calls += 1
        return {"answer_status": "answered", "answer": "The rule prohibits best.",
                "evidence": [{"passage_id": str(PASSAGE), "supporting_quote": self.quote}]}


def evidence():
    return [EvidencePassage(PASSAGE, SOURCE, VERSION, "SRC#1", "1.0", "Voice",
                            "Do not say best.", "guidelines", date(2026, 1, 1), None,
                            FusedCandidate(str(PASSAGE), 0.03, 1, 1))]


def test_followup_snapshots_before_provider_and_publishes_exact_citation(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    db = FakeDB()
    chat = Chat(db)
    chat.last_provider = "indierouter"
    chat.last_model = "fallback-model"
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: evidence())
    answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))
    assert chat.calls == 1
    assert db.commits == 2
    assert any("o.time_zone AS timezone" in sql for sql, _ in db.calls)
    assert next(p for sql, p in db.calls if "INSERT INTO chat_evidence" in sql)["supplied_text"] == "Do not say best."
    assert next(p for sql, p in db.calls if "INSERT INTO chat_citations" in sql)["excerpt"] == "Do not say best."
    assert any("state='completed'" in sql for sql, _ in db.calls)
    published = next(p for sql, p in db.calls if "answer_status=:answer_status" in sql)
    assert (published["provider_id"], published["model_id"]) == ("indierouter", "fallback-model")


def test_no_current_evidence_abstains_without_provider(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    db = FakeDB()
    chat = Chat(db)
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: [])
    answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))
    assert chat.calls == 0
    published = next(p for sql, p in db.calls if "answer_status=:answer_status" in sql)
    assert (published["provider_id"], published["model_id"]) == (None, None)
    assert not any("INSERT INTO chat_citations" in sql for sql, _ in db.calls)
    assert next(p for sql, p in db.calls if "answer_status=:answer_status" in sql)["answer_status"] == "insufficient_evidence"


def test_inexact_citation_rejected_before_publication(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    db = FakeDB()
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: evidence())
    with pytest.raises(AnswerFailure, match="invalid_answer_output"):
        answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(),
                        chat=Chat(db, quote="not in guidance"), today=date(2026, 11, 4))
    assert not any("INSERT INTO chat_citations" in sql for sql, _ in db.calls)


def test_rewrite_guard_preserves_numbers_negation_and_quoted_terms():
    assert _preserves_terms('Was "best" not allowed in 2026?', 'Was "best" not allowed in 2026?')
    assert not _preserves_terms('Was "best" not allowed in 2026?', 'Was best allowed in 2026?')
    assert not _preserves_terms('Was 2026 price approved?', 'Was price approved?')


def test_rewrite_and_answer_record_independent_provider_models(monkeypatch):
    from types import SimpleNamespace
    import app.jobs.handlers.answer_question as handler

    db = FakeDB()

    class MixedChat(Chat):
        def complete_json(self, messages, *, schema_name, schema, max_tokens):
            if schema_name == "veriloom_rewrite_followup_v2":
                self.last_provider, self.last_model = "indierouter", "rewrite-model"
                return {"needs_clarification": False, "standalone_query": "May we say best?",
                        "clarification_question": None}
            result = super().complete_json(messages, schema_name=schema_name,
                                           schema=schema, max_tokens=max_tokens)
            self.last_provider, self.last_model = "sarvam", "answer-model"
            return result

    monkeypatch.setattr(handler, "route_followup", lambda *_args, **_kw:
                        SimpleNamespace(route="rewrite_retrieve"))
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: evidence())
    answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=MixedChat(db),
                    today=date(2026, 11, 4))
    snapshot = next(p for sql, p in db.calls if "rewrite_provider_id=:rewrite_provider_id" in sql)
    published = next(p for sql, p in db.calls if "answer_status=:answer_status" in sql)
    assert (snapshot["rewrite_provider_id"], snapshot["rewrite_model_id"]) == ("indierouter", "rewrite-model")
    assert (published["provider_id"], published["model_id"]) == ("sarvam", "answer-model")
