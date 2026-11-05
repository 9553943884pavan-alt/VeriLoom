from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import replace
import json
from uuid import UUID

import pytest

from app.jobs.handlers.review_revision import ReviewFailure, review_revision
from app.jobs.handlers.review_revision import _queries, _retrieve, _retrieve_with_provenance
from app.schemas.drafts import DecisionRequest
from app.services import reviews
from app.rag.retrieval.fusion import FusedCandidate
from app.rag.retrieval.hybrid import EvidencePassage, normalize_search_query, query_within_bounds

ORG = UUID("11111111-1111-4111-8111-111111111111")
REVIEW = UUID("22222222-2222-4222-8222-222222222222")
REVISION = UUID("33333333-3333-4333-8333-333333333333")
PASSAGE = UUID("44444444-4444-4444-8444-444444444444")
SOURCE = UUID("55555555-5555-4555-8555-555555555555")
VERSION = UUID("66666666-6666-4666-8666-666666666666")
REVIEWER = UUID("88888888-8888-4888-8888-888888888888")
DECISION = UUID("99999999-9999-4999-8999-999999999999")


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
    def __init__(self, draft="Try our best coffee"):
        self.draft = draft
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
                           "content_nfc": self.draft, "channel": "social_post",
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
    chat.last_provider = "indierouter"
    chat.last_model = "fallback-model"
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
    published = next(params for sql, params in db.calls if "coverage=:coverage" in sql)
    assert (published["provider_id"], published["model_id"]) == ("indierouter", "fallback-model")
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
    assert (update["provider_id"], update["model_id"]) == (None, None)
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


class WordEmbedder(Embedder):
    def count_tokens(self, full_input):
        return len(full_input.split()) + 2


@pytest.mark.parametrize("draft", [
    " " * 4100 + "Cardamom Chai contains 120 calories per cup.",
    "Cardamom" + " \t\n" * 1800 + "Chai contains 120 calories.",
    "हिंदी café e\u0301", "a" * 4100, "word " * 1200,
])
def test_query_planner_uses_shared_bounds_on_normalized_search_text(draft):
    queries, truncated = _queries(draft, WordEmbedder())

    assert truncated is False
    assert queries
    assert all(query_within_bounds(query, WordEmbedder()) for query in queries)
    assert "".join(queries).replace(" ", "") == normalize_search_query(draft).replace(" ", "")


def test_long_query_planner_preserves_unpunctuated_line_boundaries():
    lines = ["alpha " * 60, "beta " * 60]
    queries, truncated = _queries("\n".join(lines), Embedder())

    assert queries == [normalize_search_query(line) for line in lines]
    assert truncated is False


@pytest.mark.parametrize("query_count", [3, 8])
def test_all_queries_execute_before_bounded_selection_and_tail_evidence_is_retained(monkeypatch, query_count):
    import app.jobs.handlers.review_revision as handler

    draft = " ".join(f"claim{i} " + "x" * 450 + "." for i in range(query_count))
    queries, _ = _queries(draft, Embedder())
    calls = []
    first_results = [replace(evidence()[0], passage_id=UUID(int=index)) for index in range(1, 9)]
    tail = replace(evidence()[0], passage_id=UUID(int=20))

    def retrieve(*args, **kwargs):
        calls.append(kwargs["query"])
        assert kwargs["top_k"] == 8
        return [tail] if kwargs["query"] == queries[-1] else first_results

    monkeypatch.setattr(handler, "retrieve_evidence", retrieve)
    selected, scores, incomplete = _retrieve_with_provenance(
        None, org_id=ORG, corpus_version=7, as_of=date(2026, 11, 4),
        draft=draft, embedder=Embedder(),
    )

    assert calls == queries
    assert len(selected) == len({item.passage_id for item in selected}) == 8
    assert selected[:2] == [first_results[0], tail]
    assert incomplete is False
    assert scores[tail.passage_id]["query_matches"] == [{
        "query_index": query_count, "query_text": queries[-1], "result_rank": 1,
        "rrf_score": 0.03, "dense_rank": 1, "lexical_rank": 1,
    }]
    assert [match["query_index"] for match in scores[first_results[0].passage_id]["query_matches"]] == list(range(1, query_count))
    # Diagnostic and evaluation scripts continue to consume the original pair.
    compatible_passages, compatible_incomplete = _retrieve(
        None, org_id=ORG, corpus_version=7, as_of=date(2026, 11, 4),
        draft=draft, embedder=Embedder(),
    )
    assert (compatible_passages, compatible_incomplete) == (selected, incomplete)


def test_opt_in_claim_queries_preserve_purpose_provenance(monkeypatch):
    import app.jobs.handlers.review_revision as handler

    draft = "Use code CHAI50 for 50% off until tonight."
    purpose_passage = replace(evidence()[0], passage_id=UUID(int=20))
    calls = []

    def retrieve(*args, **kwargs):
        calls.append(kwargs["query"])
        return [purpose_passage] if "promotional offers" in kwargs["query"] else evidence()

    monkeypatch.setattr(handler, "retrieve_evidence", retrieve)
    selected, scores, incomplete = _retrieve_with_provenance(
        None, org_id=ORG, corpus_version=7, as_of=date(2026, 11, 4),
        draft=draft, embedder=Embedder(), purpose_queries=True,
    )

    assert calls[0] == draft
    assert len(calls) == 2
    assert "promotional offers" in calls[1]
    assert len(selected) == 2 and incomplete is False
    purpose_scores = scores[purpose_passage.passage_id]
    assert purpose_scores["query_count"] == 2
    assert purpose_scores["query_planner_version"] == "purpose_queries/1.0.0"
    assert purpose_scores["query_matches"][0]["query_index"] == 2
    assert purpose_scores["query_matches"][0]["query_text"] == calls[1]


def test_review_worker_keeps_stage1_planning_default_after_holdout(monkeypatch):
    import app.jobs.handlers.review_revision as handler

    draft = "Use code CHAI50 for 50% off until tonight."
    db = FakeDB(draft)
    chat = CoverageChat(db)
    calls = []

    def retrieve(*args, **kwargs):
        calls.append(kwargs["query"])
        return evidence()

    monkeypatch.setattr(handler, "retrieve_evidence", retrieve)
    review_revision(db, org_id=ORG, review_id=REVIEW, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))
    assert calls == [draft]
    snapshot = next(params for sql, params in db.calls if "INSERT INTO review_evidence" in sql)
    assert json.loads(snapshot["scores"])["query_planner_version"] == "segment_queries/1.0.0"
    assert chat.user_data["draft_text"] == draft


class CoverageChat:
    model = "offline-model"

    def __init__(self, db, *, coverage="sufficient", findings=None):
        self.db = db
        self.coverage = coverage
        self.findings = findings or []
        self.user_data = None

    def complete_json(self, messages, **kwargs):
        assert self.db.commits == 1
        self.user_data = json.loads(messages[-1]["content"])
        return {"coverage": self.coverage,
                "coverage_reason": None if self.coverage == "sufficient" else "Human verification required.",
                "findings": self.findings}


@pytest.mark.parametrize("missing_query", [False, True])
def test_unprocessed_or_evidence_free_segments_cannot_publish_sufficient_coverage(monkeypatch, missing_query):
    import app.jobs.handlers.review_revision as handler

    segment_count = 3 if missing_query else 10
    draft = " ".join(f"claim{i} " + "x" * 450 + "." for i in range(segment_count))
    queries, _ = _queries(draft, Embedder())
    db = FakeDB(draft)
    chat = CoverageChat(db)
    calls = []

    def retrieve(*args, **kwargs):
        calls.append(kwargs["query"])
        return [] if missing_query and kwargs["query"] == queries[-1] else evidence()

    monkeypatch.setattr(handler, "retrieve_evidence", retrieve)
    review_revision(db, org_id=ORG, review_id=REVIEW, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))

    assert calls == queries
    published = next(params for sql, params in db.calls if "coverage=:coverage" in sql)
    assert published["coverage"] == "partial"
    assert "omitted from retrieval or has no selected evidence" in published["coverage_reason"]
    scores = json.loads(next(params["scores"] for sql, params in db.calls if "INSERT INTO review_evidence" in sql))
    assert scores["query_truncated"] is (not missing_query)
    assert scores["uncovered_query_indexes"] == ([3] if missing_query else [])
    assert scores["query_count"] == len(queries)
    assert chat.user_data["draft_text"] == draft


@pytest.mark.parametrize("coverage, expected", [("sufficient", "partial"), ("partial", "partial"),
                                                 ("insufficient", "insufficient")])
def test_unsupported_claims_preclude_sufficient_coverage_and_never_promote_coverage(monkeypatch, coverage, expected):
    import app.jobs.handlers.review_revision as handler

    db = FakeDB()
    chat = CoverageChat(db, coverage=coverage, findings=[{
        "category": "promotional_claim", "severity": "high", "evidence_status": "insufficient",
        "quote": "best", "explanation": "A human must verify this claim.", "suggested_edit": None,
        "evidence": [],
    }])
    monkeypatch.setattr(handler, "retrieve_evidence", lambda *args, **kwargs: evidence())
    review_revision(db, org_id=ORG, review_id=REVIEW, embedder=Embedder(), chat=chat,
                    today=date(2026, 11, 4))

    published = next(params for sql, params in db.calls if "coverage=:coverage" in sql)
    assert published["coverage"] == expected
    assert published["coverage_reason"]
    assert not any("INSERT INTO finding_citations" in sql for sql, _ in db.calls)


def test_search_normalization_preserves_immutable_prompt_text_and_codepoint_anchors(monkeypatch):
    import app.jobs.handlers.review_revision as handler

    draft = " \n" * 2100 + "हिंदी café best coffee"
    db = FakeDB(draft)
    chat = Chat(db)
    search_queries = []
    prompt_drafts = []
    complete = chat.complete_json

    def capture(messages, **kwargs):
        prompt_drafts.append(json.loads(messages[-1]["content"])["draft_text"])
        return complete(messages, **kwargs)

    def retrieve(*args, **kwargs):
        search_queries.append(kwargs["query"])
        return evidence()

    chat.complete_json = capture
    monkeypatch.setattr(handler, "retrieve_evidence", retrieve)
    review_revision(db, org_id=ORG, review_id=REVIEW, embedder=WordEmbedder(), chat=chat,
                    today=date(2026, 11, 4))

    assert search_queries == ["हिंदी café best coffee"]
    assert prompt_drafts == [draft]
    finding = next(params for sql, params in db.calls if "INSERT INTO findings" in sql)
    assert finding["anchor_start"] == draft.index("best")
    assert draft[finding["anchor_start"]:finding["anchor_end"]] == "best"
    scores = json.loads(next(params["scores"] for sql, params in db.calls if "INSERT INTO review_evidence" in sql))
    assert scores["query_matches"][0]["query_text"] == search_queries[0]


def test_decision_idempotency_payload_encodes_datetime(monkeypatch):
    class EmptyResult:
        def mappings(self):
            return self

        def first(self):
            return None

    class DecisionDB:
        def __init__(self):
            self.calls = []
            self.commits = 0

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            return EmptyResult()

        def commit(self):
            self.commits += 1

    decided_at = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(reviews, "_require_role", lambda *_args, **_kwargs: ["reviewer"])
    monkeypatch.setattr(reviews.repo, "lock_review_decision", lambda *_args, **_kwargs: {
        "reviewer_user_id": REVIEWER,
        "creator_user_id": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "review_version": 3,
        "revision_id": REVISION,
        "current_revision_id": REVISION,
        "state": "ready_for_review",
        "draft_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    })
    monkeypatch.setattr(reviews.repo, "has_override_trigger", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(reviews.repo, "insert_decision", lambda *_args, **_kwargs: {
        "decision_id": DECISION,
        "decided_at": decided_at,
    })
    db = DecisionDB()

    result = reviews.decide(
        db,
        org_id=ORG,
        review_id=REVIEW,
        user_id=REVIEWER,
        payload=DecisionRequest(
            expected_revision_id=REVISION,
            expected_review_version=3,
            action="approve",
        ),
        idempotency_key=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    )

    saved = next(params for sql, params in db.calls if "INSERT INTO idempotency_records" in sql)
    encoded_response = json.loads(saved["response"])
    assert encoded_response["decision_id"] == str(DECISION)
    assert encoded_response["decided_at"] == "2026-11-04T12:00:00+00:00"
    assert result["decided_at"] == decided_at
    assert db.commits == 1
