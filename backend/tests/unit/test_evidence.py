"""Authorization and query boundary tests for evidence passage reads."""

from uuid import UUID

import pytest

from app.core.errors import ApiError
from app.db.repositories import evidence as repo
from app.services import evidence
from app.main import create_app


ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
PASSAGE_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_admin_can_read_historical_passage_without_a_citation(monkeypatch: pytest.MonkeyPatch) -> None:
    passage = {"passage_id": PASSAGE_ID, "current_status": "archived"}
    monkeypatch.setattr(repo, "get_passage", lambda *_args, **_kwargs: passage)
    monkeypatch.setattr(evidence, "_require_role", lambda *_args: ["admin"])

    def fail_if_checked(*_args, **_kwargs):
        pytest.fail("Admin reads should not require a citation")

    monkeypatch.setattr(repo, "user_may_read_cited_passage", fail_if_checked)

    assert evidence.get_passage(object(), org_id=ORG_ID, passage_id=PASSAGE_ID, user_id=USER_ID) == passage


@pytest.mark.parametrize("roles", [["creator"], ["reviewer"], ["creator", "reviewer"]])
def test_non_admin_must_be_a_permitted_citation_participant(
    monkeypatch: pytest.MonkeyPatch, roles: list[str]
) -> None:
    passage = {"passage_id": PASSAGE_ID, "current_status": "expired"}
    checked = {}
    monkeypatch.setattr(repo, "get_passage", lambda *_args, **_kwargs: passage)
    monkeypatch.setattr(evidence, "_require_role", lambda *_args: roles)
    monkeypatch.setattr(
        repo,
        "user_may_read_cited_passage",
        lambda *_args, **kwargs: checked.update(kwargs) or True,
    )

    assert evidence.get_passage(object(), org_id=ORG_ID, passage_id=PASSAGE_ID, user_id=USER_ID) == passage
    assert checked["is_creator"] is ("creator" in roles)
    assert checked["is_reviewer"] is ("reviewer" in roles)


def test_nonparticipant_cannot_read_an_uncited_passage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo, "get_passage", lambda *_args, **_kwargs: {"passage_id": PASSAGE_ID})
    monkeypatch.setattr(evidence, "_require_role", lambda *_args: ["reviewer"])
    monkeypatch.setattr(repo, "user_may_read_cited_passage", lambda *_args, **_kwargs: False)

    with pytest.raises(ApiError) as error:
        evidence.get_passage(object(), org_id=ORG_ID, passage_id=PASSAGE_ID, user_id=USER_ID)

    assert error.value.status_code == 404
    assert error.value.code == "not_found"


def test_missing_or_pending_passage_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo, "get_passage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(evidence, "_require_role", lambda *_args: ["admin"])

    with pytest.raises(ApiError) as error:
        evidence.get_passage(object(), org_id=ORG_ID, passage_id=PASSAGE_ID, user_id=USER_ID)

    assert error.value.status_code == 404


def test_nonmember_cannot_probe_whether_passage_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise ApiError(403, "forbidden", "No organisation access.")

    monkeypatch.setattr(evidence, "_require_role", forbidden)
    monkeypatch.setattr(repo, "get_passage", lambda *_args, **_kwargs: pytest.fail("passage must not be queried"))
    with pytest.raises(ApiError) as error:
        evidence.get_passage(object(), org_id=ORG_ID, passage_id=PASSAGE_ID, user_id=USER_ID)
    assert error.value.status_code == 403


def test_repository_scopes_to_tenant_and_filters_pending_versions() -> None:
    class Result:
        def mappings(self):
            return self

        def first(self):
            return None

    class Db:
        statement = ""

        def execute(self, statement, _params):
            self.statement = str(statement)
            return Result()

    db = Db()
    assert repo.get_passage(db, org_id=ORG_ID, passage_id=PASSAGE_ID) is None
    assert "p.org_id = :org_id AND p.id = :passage_id" in db.statement
    assert "v.approval_status IN ('approved', 'archived')" in db.statement
    assert "v.ingestion_status = 'ready'" in db.statement
    assert "timezone(o.time_zone, now())::date" in db.statement


def test_participant_query_requires_a_real_citation_and_review_participation() -> None:
    class Result:
        def scalar_one(self):
            return False

    class Db:
        statement = ""

        def execute(self, statement, _params):
            self.statement = str(statement)
            return Result()

    db = Db()
    assert not repo.user_may_read_cited_passage(
        db,
        org_id=ORG_ID,
        passage_id=PASSAGE_ID,
        user_id=USER_ID,
        is_creator=True,
        is_reviewer=False,
    )
    assert "finding_citations" in db.statement
    assert "chat_citations" in db.statement
    assert "d.creator_user_id = :user_id" in db.statement
    assert "r.reviewer_user_id = :user_id" in db.statement


def test_evidence_route_is_mounted_under_api_v1() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/organisations/{org_id}/evidence/{passage_id}" in paths
