"""Focused draft API and normalization checks."""

from uuid import UUID
import hashlib
import json

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.schemas.drafts import DraftCreate
from app.services import drafts
from app.core.errors import ApiError


ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_draft_text_limit_is_codepoint_bounded() -> None:
    with pytest.raises(ValidationError):
        DraftCreate(title="Brief", channel="social", content="x" * 20001)


def test_create_draft_requires_creator_and_normalizes_text(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    monkeypatch.setattr(drafts, "_require_role", lambda *_args: ["creator"])
    inserted: dict = {}
    monkeypatch.setattr(drafts.repo, "insert_draft", lambda _db, **kwargs: inserted.update(kwargs) or {
        "draft_id": USER_ID, "state": "editing", "working_copy_version": 1,
    })
    result = drafts.create(db, org_id=ORG_ID, user_id=USER_ID,
                           payload=DraftCreate(title="Brief", channel="social", content="e\u0301"))
    assert inserted["content"] == "é"
    assert result["state"] == "editing"
    assert db.commits == 1


def test_draft_and_review_routes_are_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/organisations/{org_id}/drafts/{draft_id}/submissions" in paths
    assert "/api/v1/organisations/{org_id}/reviews/{review_id}/decisions" in paths


def test_submission_replay_is_returned_before_current_draft_state(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = UUID("33333333-3333-4333-8333-333333333333")
    reviewer_id = UUID("44444444-4444-4444-8444-444444444444")
    draft_id = UUID("55555555-5555-4555-8555-555555555555")
    key = UUID("66666666-6666-4666-8666-666666666666")
    from app.schemas.drafts import DraftSubmit
    payload = DraftSubmit(expected_working_copy_version=1, reviewer_user_id=reviewer_id)
    body = json.dumps({"expected_working_copy_version":1,"reviewer_user_id":str(reviewer_id)}, sort_keys=True)
    digest = hashlib.sha256((f"{ORG_ID}\0{draft_id}\0"+body).encode()).hexdigest()
    replay = {"revision_id":str(USER_ID),"revision_number":1,"review_id":str(ORG_ID),
              "draft_state":"processing","review_state":"queued",
              "status_url":f"/api/v1/organisations/{ORG_ID}/reviews/{ORG_ID}"}

    class Rows:
        def mappings(self):
            return self
        def first(self):
            return {"org_id":ORG_ID,"request_sha256":digest,"response_json":replay,"expired":False}

    class ReplayDb(FakeDb):
        def execute(self, *_args, **_kwargs):
            return Rows()
        def rollback(self):
            pass

    monkeypatch.setattr(drafts, "_require_role", lambda *_args: ["creator"])
    monkeypatch.setattr(drafts.repo, "lock_draft_for_submit", lambda *_a, **_k: pytest.fail("replay must precede state lookup"))
    assert drafts.submit(ReplayDb(), org_id=ORG_ID, draft_id=draft_id, user_id=user_id,
                         payload=payload, idempotency_key=key) == replay


def test_submission_rechecks_idempotency_after_waiting_for_draft_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.drafts import DraftSubmit

    reviewer_id = UUID("44444444-4444-4444-8444-444444444444")
    draft_id = UUID("55555555-5555-4555-8555-555555555555")
    key = UUID("66666666-6666-4666-8666-666666666666")
    payload = DraftSubmit(expected_working_copy_version=1, reviewer_user_id=reviewer_id)
    body = json.dumps({"expected_working_copy_version": 1, "reviewer_user_id": str(reviewer_id)}, sort_keys=True)
    digest = hashlib.sha256((f"{ORG_ID}\0{draft_id}\0" + body).encode()).hexdigest()
    replay = {"revision_id": str(USER_ID), "review_id": str(ORG_ID), "draft_state": "processing"}

    class Rows:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def first(self):
            return self.value

    class RacingDb(FakeDb):
        def __init__(self):
            super().__init__()
            self.lookups = 0
            self.rollbacks = 0

        def execute(self, *_args, **_kwargs):
            self.lookups += 1
            return Rows(None if self.lookups == 1 else {
                "org_id": ORG_ID, "request_sha256": digest,
                "response_json": replay, "expired": False,
            })

        def rollback(self):
            self.rollbacks += 1

    db = RacingDb()
    monkeypatch.setattr(drafts, "_require_role", lambda *_args: ["creator"])
    monkeypatch.setattr(drafts.repo, "lock_draft_for_submit", lambda *_a, **_k: {
        "creator_user_id": USER_ID, "state": "processing", "working_copy_version": 1,
    })
    assert drafts.submit(db, org_id=ORG_ID, draft_id=draft_id, user_id=USER_ID,
                         payload=payload, idempotency_key=key) == replay
    assert db.lookups == 2 and db.rollbacks == 1


def test_draft_detail_hides_other_tenant_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drafts.repo, "get_draft", lambda *_a, **_k: {"draft_id":USER_ID,"creator_user_id":USER_ID})
    monkeypatch.setattr(drafts, "_require_role", lambda *_a: ["creator"])
    with pytest.raises(ApiError) as denied:
        drafts.detail(FakeDb(), org_id=ORG_ID, draft_id=USER_ID,
                      user_id=UUID("77777777-7777-4777-8777-777777777777"))
    assert denied.value.status_code == 404


def test_revision_history_is_bounded_and_uses_number_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drafts, "detail", lambda *_args, **_kwargs: {"draft_id": USER_ID})
    captured = {}

    def list_revisions(_db, **kwargs):
        captured.update(kwargs)
        return [
            {"revision_number": 3}, {"revision_number": 2}, {"revision_number": 1},
        ]

    monkeypatch.setattr(drafts.repo, "list_revisions", list_revisions)
    result = drafts.revisions(
        FakeDb(), org_id=ORG_ID, draft_id=USER_ID, user_id=USER_ID,
        cursor="4", limit=2,
    )
    assert captured["before"] == 4 and captured["limit"] == 3
    assert [item["revision_number"] for item in result["items"]] == [3, 2]
    assert result["next_cursor"] == "2"
    with pytest.raises(ApiError):
        drafts.revisions(
            FakeDb(), org_id=ORG_ID, draft_id=USER_ID, user_id=USER_ID,
            cursor="bad", limit=2,
        )


def test_review_followup_routes_are_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/organisations/{org_id}/reviews/{review_id}/questions" in paths
    assert "/api/v1/organisations/{org_id}/reviews/{review_id}/questions/{turn_id}" in paths
    assert "/api/v1/organisations/{org_id}/reviews/{review_id}/retry" in paths


def test_review_cursor_encodes_creation_timestamp_and_id() -> None:
    from datetime import datetime, timezone
    import base64
    import json

    from app.services.reviews import _cursor, _page

    first_id = UUID("11111111-1111-4111-8111-111111111111")
    created = datetime(2026, 11, 4, 10, 30, tzinfo=timezone.utc)
    page = _page([
        {"comment_id": first_id, "created_at": created},
        {"comment_id": UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"), "created_at": created},
    ], 1, "comment_id")
    cursor = page["next_cursor"]
    encoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert encoded == [created.isoformat(), str(first_id)]
    assert _cursor(cursor) == (created, first_id)
