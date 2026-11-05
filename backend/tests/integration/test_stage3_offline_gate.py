"""Synthetic validation, publish, and reviewer HTTP seam without external services.

This is deliberately narrower than the disposable PostgreSQL/live-provider gate.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.v1.organisations import signed_in_user
from app.db.session import get_session
from app.jobs.handlers.review_revision import _publish
from app.main import create_app
from app.rag.retrieval.fusion import FusedCandidate
from app.rag.retrieval.hybrid import EvidencePassage
from app.rag.review_validation import validate_review
from app.services import reviews


ORG = UUID("11111111-1111-4111-8111-111111111111")
OTHER_ORG = UUID("22222222-2222-4222-8222-222222222222")
REVIEW = UUID("33333333-3333-4333-8333-333333333333")
REVISION = UUID("44444444-4444-4444-8444-444444444444")
REVIEWER = UUID("55555555-5555-4555-8555-555555555555")
PASSAGE = UUID("66666666-6666-4666-8666-666666666666")
FINDING = UUID("77777777-7777-4777-8777-777777777777")
DRAFT = "Our cafe is the best. It has 120 calories."
GUIDANCE = "Do not claim best. Preferred term: Cardamom Chai."


class Result:
    def __init__(self, row=None, scalar=None):
        self.row, self.scalar = row, scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def scalar_one(self):
        return self.scalar


class PublishRecorder:
    def __init__(self):
        self.statements = []
        self.commits = 0

    def execute(self, statement, params):
        sql = str(statement)
        self.statements.append((sql, params))
        if "SELECT r.state,r.revision_id,d.current_revision_id" in sql:
            return Result({"state": "processing", "revision_id": REVISION,
                           "current_revision_id": REVISION})
        if "INSERT INTO findings" in sql:
            return Result(scalar=FINDING)
        return Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        raise AssertionError("publish unexpectedly rolled back")


def _candidate():
    return {"coverage": "sufficient", "coverage_reason": None, "findings": [
        {"category": "promotional_claim", "severity": "high",
         "evidence_status": "supported", "quote": "best",
         "explanation": "The guidance prohibits this claim.",
         "suggested_edit": None,
         "evidence": [{"passage_id": str(PASSAGE),
                       "supporting_quote": "Do not claim best."}]},
        {"category": "factual_claim", "severity": "high",
         "evidence_status": "supported", "quote": "120 calories",
         "explanation": "The calorie claim is not established by this terminology rule.",
         "suggested_edit": None,
         "evidence": [{"passage_id": str(PASSAGE),
                       "supporting_quote": "Preferred term: Cardamom Chai."}]},
    ]}


def test_opt_in_filter_publish_and_reviewer_response(monkeypatch):
    baseline = validate_review(_candidate(), revision_text=DRAFT,
                               supplied_passages={PASSAGE: GUIDANCE})
    assert len(baseline.findings) == 2

    filtered = validate_review(_candidate(), revision_text=DRAFT,
                               supplied_passages={PASSAGE: GUIDANCE}, quality_filter=True)
    assert filtered.coverage == "partial"
    assert len(filtered.findings) == 1
    assert filtered.findings[0].candidate.quote == "best"
    assert (filtered.findings[0].anchor_start, filtered.findings[0].anchor_end) == (16, 20)

    passage = EvidencePassage(PASSAGE, UUID(int=8), UUID(int=9), "SYN#P1", "1.0",
                              "Synthetic guidance", GUIDANCE, "guidelines",
                              date(2026, 1, 1), None,
                              FusedCandidate(str(PASSAGE), 0.03, 1, 1))
    db = PublishRecorder()
    _publish(db, org_id=ORG, review_id=REVIEW, revision_id=REVISION,
             draft=DRAFT, snapshot=[passage], validated=filtered,
             provider_id="indierouter", model_id="synthetic-model")
    assert db.commits == 1
    finding_params = [params for sql, params in db.statements if "INSERT INTO findings" in sql]
    citation_params = [params for sql, params in db.statements if "INSERT INTO finding_citations" in sql]
    review_params = [params for sql, params in db.statements if "coverage=:coverage" in sql]
    assert len(finding_params) == len(citation_params) == len(review_params) == 1
    assert finding_params[0]["quote"] == "best"
    assert citation_params[0]["excerpt"] == "Do not claim best."
    assert review_params[0]["coverage"] == "partial"
    assert (review_params[0]["provider_id"], review_params[0]["model_id"]) == (
        "indierouter", "synthetic-model")

    # Exercise the actual reviewer route and service formatting with persisted
    # write parameters represented by the recorder. No database is implied.
    monkeypatch.setattr(reviews.repo, "get_review", lambda _db, *, org_id, review_id:
                        {"review_id": review_id, "draft_id": UUID(int=10),
                         "revision_id": REVISION, "revision_number": 1,
                         "assigned_reviewer_user_id": REVIEWER, "state": "ready_for_review",
                         "coverage": review_params[0]["coverage"],
                         "coverage_reason": review_params[0]["coverage_reason"],
                         "review_version": 0, "reviewed_against_corpus_version": 1,
                         "error_code": None, "created_at": None, "completed_at": None,
                         "creator_user_id": UUID(int=11), "current_revision_id": REVISION}
                        if org_id == ORG and review_id == REVIEW else None)
    monkeypatch.setattr(reviews.repo, "findings", lambda _db, *, org_id, review_id:
                        [{"finding_id": FINDING, "category": finding_params[0]["category"],
                          "severity": finding_params[0]["severity"],
                          "evidence_status": finding_params[0]["evidence_status"],
                          "anchor_start": finding_params[0]["anchor_start"],
                          "anchor_end": finding_params[0]["anchor_end"],
                          "quote": finding_params[0]["quote"],
                          "explanation": finding_params[0]["explanation"],
                          "suggested_edit": finding_params[0]["suggested_edit"],
                          "citations": [{"passage_id": PASSAGE,
                                         "excerpt": citation_params[0]["excerpt"]}]}]
                        if org_id == ORG and review_id == REVIEW else [])
    monkeypatch.setattr(reviews.repo, "latest_decision", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(reviews, "_require_role", lambda _db, org_id, user_id, _roles:
                        {"reviewer"} if org_id == ORG and user_id == REVIEWER else set())

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[signed_in_user] = lambda: {"id": REVIEWER}
    with TestClient(app) as client:
        response = client.get(f"/api/v1/organisations/{ORG}/reviews/{REVIEW}")
        assert response.status_code == 200
        body = response.json()
        assert body["coverage"] == "partial"
        assert len(body["findings"]) == 1
        assert body["findings"][0]["anchor"] == {
            "start_codepoint": 16, "end_codepoint": 20, "quote": "best"}
        assert body["findings"][0]["citations"][0]["excerpt"] == "Do not claim best."
        assert client.get(f"/api/v1/organisations/{OTHER_ORG}/reviews/{REVIEW}").status_code == 404
