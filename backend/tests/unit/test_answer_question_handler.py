from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json
from uuid import UUID

import pytest

from app.jobs.handlers.answer_question import AnswerFailure, _preserves_terms, _select, answer_question
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
                               "coverage": "sufficient", "coverage_reason": None,
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
    scores = json.loads(next(p for sql, p in db.calls if "INSERT INTO chat_evidence" in sql)["scores"])
    assert scores["query_planner_version"] == "single_query/1.0.0"
    assert next(p for sql, p in db.calls if "INSERT INTO chat_citations" in sql)["excerpt"] == "Do not say best."
    assert any("state='completed'" in sql for sql, _ in db.calls)
    published = next(p for sql, p in db.calls if "answer_status=:answer_status" in sql)
    assert (published["provider_id"], published["model_id"]) == ("indierouter", "fallback-model")


def test_padded_followup_query_uses_normalized_search_bounds(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    question = "May" + " " * 700 + "we say best?"

    class PaddedDB(FakeDB):
        def execute(self, statement, params):
            result = super().execute(statement, params)
            if "SELECT t.id,t.review_id" in str(statement):
                result.row["question"] = question
            return result

    db = PaddedDB()
    searched = []

    def retrieve(_db, **kwargs):
        searched.append(kwargs["query"])
        return evidence()

    monkeypatch.setattr(handler, "retrieve_evidence", retrieve)
    _, selected, _, route, _, stored_query = _select(
        db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=Chat(db),
        today=date(2026, 11, 4),
    )
    assert route == "fresh_retrieve"
    assert stored_query == question
    assert searched == ["May we say best?"]
    assert selected[0]["passage_id"] == PASSAGE


def test_review_coverage_question_explains_stored_reason_without_brand_citation(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    class CoverageDB(FakeDB):
        def execute(self, statement, params):
            result = super().execute(statement, params)
            if "SELECT t.id,t.review_id" in str(statement):
                result.row.update({
                    "question": "How it is insufficient",
                    "coverage": "insufficient",
                    "coverage_reason": "No approved Naruto guidance is available for this review.",
                })
            return result

    db = CoverageDB()
    chat = Chat(db)
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *_args, **_kwargs:
                        pytest.fail("coverage diagnostic must not search brand evidence"))
    answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))

    assert chat.calls == 0
    published = next(params for sql, params in db.calls
                     if "answer_status=:answer_status" in sql)
    assert (published["route"], published["answer_status"]) == ("abstain", "insufficient_evidence")
    assert "No approved Naruto guidance" in published["answer"]
    assert "stored review diagnostic" in published["answer"]
    assert not any("INSERT INTO chat_citations" in sql for sql, _ in db.calls)


def test_selected_finding_suggestion_is_context_for_grounded_refinement(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    finding_id = UUID("88888888-8888-4888-8888-888888888888")

    class FindingDB(FakeDB):
        def execute(self, statement, params):
            sql = str(statement)
            if "SELECT quote,explanation,suggested_edit FROM findings" in sql:
                self.calls.append((sql, params))
                return Result(row={"quote": "best", "explanation": "Avoid unproved superlatives.",
                                   "suggested_edit": "Our coffee is fresh."})
            result = super().execute(statement, params)
            if "SELECT t.id,t.review_id" in sql:
                result.row["finding_id"] = finding_id
                result.row["question"] = "Can you improve this suggested edit?"
            return result

    class RefinementChat(Chat):
        def complete_json(self, messages, *, schema_name, schema, max_tokens):
            payload = json.loads(messages[-1]["content"])
            assert payload["finding"]["suggested_edit"] == "Our coffee is fresh."
            if schema_name == "veriloom_rewrite_followup_v2":
                assert payload["review_coverage"]["status"] == "sufficient"
                return {"needs_clarification": False,
                        "standalone_query": "Approved wording for coffee freshness",
                        "clarification_question": None}
            assert schema_name == "veriloom_grounded_answer_v2"
            assert payload["current_evidence"][0]["text"] == "Do not say best."
            return {"answer_status": "insufficient_evidence",
                    "answer": "Current guidance does not support a freshness claim. Please verify wording with the brand administrator.",
                    "evidence": []}

    db = FindingDB()
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *_args, **_kwargs: evidence())
    answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(),
                    chat=RefinementChat(db), today=date(2026, 11, 4))
    published = next(params for sql, params in db.calls
                     if "answer_status=:answer_status" in sql)
    assert published["answer_status"] == "insufficient_evidence"
    assert published["route"] == "rewrite_retrieve"
    assert not any("UPDATE findings" in sql for sql, _ in db.calls)


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


def test_new_topic_does_not_reuse_selected_finding_evidence(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    finding_id = UUID("88888888-8888-4888-8888-888888888888")

    class FindingDB(FakeDB):
        def execute(self, statement, params):
            sql = str(statement)
            if "SELECT t.id,t.review_id" in sql:
                result = super().execute(statement, params)
                result.row["finding_id"] = finding_id
                result.row["question"] = "What source confirms the refund policy?"
                return result
            if "SELECT quote,explanation,suggested_edit FROM findings" in sql:
                self.calls.append((sql, params))
                return Result(row={"quote": "best", "explanation": "Do not say best.",
                                   "suggested_edit": "Use the approved wording."})
            if "SELECT count(*) FROM finding_citations" in sql:
                self.calls.append((sql, params))
                return Result(scalar=1)
            return super().execute(statement, params)

    db = FindingDB()
    chat = Chat(db)
    monkeypatch.setattr(handler, "_current_finding_evidence", lambda *_args, **_kwargs: [{
        "passage_id": PASSAGE, "source_ref": "SRC#1", "source_id": SOURCE,
        "version_id": VERSION, "source_version": "1.0", "title": "Voice",
        "text": "Do not say best.", "effective_from": date(2026, 1, 1),
    }])
    searches = []

    def no_refund_guidance(_db, **kwargs):
        searches.append(kwargs["query"])
        return []

    monkeypatch.setattr(handler, "retrieve_evidence", no_refund_guidance)
    answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))
    assert searches == ["What source confirms the refund policy?"]
    assert chat.calls == 0
    assert not any("INSERT INTO chat_evidence" in sql for sql, _ in db.calls)
    published = next(p for sql, p in db.calls if "answer_status=:answer_status" in sql)
    assert published["route"] == "abstain"
    assert published["answer_status"] == "insufficient_evidence"


def test_direct_finding_source_question_uses_current_exact_citation_without_model(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    finding_id = UUID("88888888-8888-4888-8888-888888888888")

    class FindingDB(FakeDB):
        def execute(self, statement, params):
            sql = str(statement)
            if "SELECT t.id,t.review_id" in sql:
                result = super().execute(statement, params)
                result.row["finding_id"] = finding_id
                result.row["question"] = "Which source supports this finding?"
                return result
            if "SELECT quote,explanation,suggested_edit FROM findings" in sql:
                self.calls.append((sql, params))
                return Result(row={"quote": "best", "explanation": "The phrase is prohibited.",
                                   "suggested_edit": "Use approved wording."})
            if "SELECT count(*) FROM finding_citations" in sql:
                self.calls.append((sql, params))
                return Result(scalar=1)
            return super().execute(statement, params)

    db = FindingDB()
    chat = Chat(db)
    monkeypatch.setattr(handler, "_current_finding_evidence", lambda *_args, **_kwargs: [{
        "passage_id": PASSAGE, "source_ref": "SRC#1", "source_id": SOURCE,
        "version_id": VERSION, "source_version": "1.0", "title": "Voice",
        "text": "Do not say best.", "cited_excerpt": "Do not say best.",
        "effective_from": date(2026, 1, 1),
    }])
    answer_question(db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))
    assert chat.calls == 0
    cited = next(p for sql, p in db.calls if "INSERT INTO chat_citations" in sql)
    assert cited["excerpt"] == "Do not say best."
    published = next(p for sql, p in db.calls if "answer_status=:answer_status" in sql)
    assert published["answer_status"] == "answered"
    assert published["route"] == "reuse_evidence"
    assert (published["provider_id"], published["model_id"]) == (None, None)


def test_followup_purpose_search_keeps_selected_evidence_query_provenance(monkeypatch):
    import app.jobs.handlers.answer_question as handler

    class RefundDB(FakeDB):
        def execute(self, statement, params):
            result = super().execute(statement, params)
            if "SELECT t.id,t.review_id" in str(statement):
                result.row["question"] = "What is the refund policy?"
            return result

    db = RefundDB()
    purpose_passage = replace(evidence()[0], passage_id=UUID(int=20))
    searches = []

    def retrieve(_db, **kwargs):
        searches.append(kwargs["query"])
        return [purpose_passage] if "authorised supporting evidence" in kwargs["query"] else evidence()

    monkeypatch.setattr(handler, "retrieve_evidence", retrieve)
    _, selected, _, route, _, stored_query = _select(
        db, org_id=ORG, turn_id=TURN, embedder=Embedder(), chat=Chat(db),
        today=date(2026, 11, 4), purpose_queries=True,
    )

    assert route == "fresh_retrieve"
    assert stored_query == searches[0] == "What is the refund policy?"
    assert len(searches) == 2
    assert [item["passage_id"] for item in selected] == [PASSAGE, purpose_passage.passage_id]
    snapshots = [params for sql, params in db.calls if "INSERT INTO chat_evidence" in sql]
    purpose_scores = json.loads(next(params["scores"] for params in snapshots
                                     if params["passage_id"] == purpose_passage.passage_id))
    assert purpose_scores["query_count"] == 2
    assert purpose_scores["query_planner_version"] == "purpose_queries/1.0.0"
    assert purpose_scores["uncovered_query_indexes"] == []
    assert purpose_scores["query_matches"][0]["query_index"] == 2
    assert purpose_scores["query_matches"][0]["query_text"] == searches[1]


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
