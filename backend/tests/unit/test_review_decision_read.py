"""Creator read access to the review's human decision reason."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.db.repositories import reviews as repo
from app.schemas.reviews import ReviewOut
from app.services import reviews


@pytest.mark.parametrize("has_decision", [False, True])
def test_creator_get_review_includes_typed_decision_without_changing_revision(monkeypatch, has_decision):
    org, review, creator, reviewer, revision = (uuid4() for _ in range(5))
    now = datetime.now(timezone.utc)
    row = dict(review_id=review, draft_id=uuid4(), revision_id=revision, revision_number=2,
               assigned_reviewer_user_id=reviewer, creator_user_id=creator,
               current_revision_id=revision, state="changes_requested" if has_decision else "ready_for_review",
               coverage="sufficient", coverage_reason=None, review_version=2,
               reviewed_against_corpus_version=1, error_code=None, created_at=now, completed_at=now)
    decision = dict(decision_id=uuid4(), action="request_changes", comment="Please remove the calorie claim.",
                    override_reason=None, decided_by_user_id=reviewer, decided_at=now) if has_decision else None
    monkeypatch.setattr(repo, "get_review", lambda *args, **kw: row.copy())
    monkeypatch.setattr(reviews, "_require_role", lambda *args: {"creator"})
    monkeypatch.setattr(repo, "findings", lambda *args, **kw: [])

    def read_decision(db, **kwargs):
        assert kwargs == dict(org_id=org, review_id=review)
        return decision

    monkeypatch.setattr(repo, "latest_decision", read_decision)
    response = ReviewOut.model_validate(reviews.get(object(), org_id=org, review_id=review, user_id=creator))
    assert response.revision_id == revision
    if has_decision:
        assert response.decision.comment == "Please remove the calorie claim."
        assert response.decision.decided_by_user_id == reviewer
    else:
        assert response.decision is None


def test_nonparticipant_cannot_read_decision(monkeypatch):
    monkeypatch.setattr(repo, "get_review", lambda *a, **kw: dict(creator_user_id=uuid4(), assigned_reviewer_user_id=uuid4()))
    monkeypatch.setattr(reviews, "_require_role", lambda *args: {"creator"})
    monkeypatch.setattr(repo, "latest_decision", lambda *a, **kw: pytest.fail("must authorize first"))
    with pytest.raises(ApiError):
        reviews.get(object(), org_id=uuid4(), review_id=uuid4(), user_id=uuid4())


def test_latest_decision_query_scopes_org_and_review_and_orders_latest():
    org, review = uuid4(), uuid4()

    class DB:
        def execute(self, statement, params):
            sql = str(statement)
            assert "org_id=:org_id AND review_id=:review_id" in sql
            assert "ORDER BY created_at DESC,id DESC LIMIT 1" in sql
            assert params == dict(org_id=org, review_id=review)
            return self

        def mappings(self):
            return self

        def first(self):
            return None

    assert repo.latest_decision(DB(), org_id=org, review_id=review) is None
