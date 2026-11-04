"""Organisation access and invitation safety without a live database."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.security import csrf_token
from app.db.session import get_session
from app.main import create_app
from app.schemas.organisations import CreateInvitationRequest, CreateOrganisationRequest, UpdateMemberRolesRequest
from app.services import organisations as service


ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
ADMIN_ID = UUID("22222222-2222-4222-8222-222222222222")
CREATOR_ID = UUID("33333333-3333-4333-8333-333333333333")


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_org_creation_validates_iana_zone_and_roles() -> None:
    assert CreateOrganisationRequest(name=" Demo ", time_zone="Asia/Kolkata").name == "Demo"
    with pytest.raises(ValidationError):
        CreateOrganisationRequest(name="Demo", time_zone="Mars/Maybe")
    with pytest.raises(ValidationError):
        UpdateMemberRolesRequest(roles=["admin", "admin"])


def test_organisation_responses_expose_corpus_version() -> None:
    from app.schemas.organisations import OrganisationCreated, OrganisationList

    row = {"org_id": ORG_ID, "name": "Demo", "roles": ["admin"], "corpus_version": 4}
    assert OrganisationList(items=[row], next_cursor=None).items[0].corpus_version == 4
    assert OrganisationCreated(**row, time_zone="UTC").corpus_version == 4
    with pytest.raises(ValidationError):
        OrganisationCreated(**{**row, "corpus_version": -1}, time_zone="UTC")


def test_member_and_reviewer_access_is_tenant_scoped(monkeypatch) -> None:
    db = FakeDb()
    monkeypatch.setattr(service.repo, "active_role", lambda _db, _org, _user: None)
    with pytest.raises(ApiError) as missing:
        service.list_members(db, ORG_ID, CREATOR_ID, None, 20)
    assert missing.value.status_code == 404

    monkeypatch.setattr(service.repo, "active_role", lambda _db, _org, _user: ["reviewer"])
    with pytest.raises(ApiError) as forbidden:
        service.list_reviewers(db, ORG_ID, CREATOR_ID, None, 20)
    assert forbidden.value.status_code == 403

    seen = {}
    monkeypatch.setattr(service.repo, "active_role", lambda _db, _org, _user: ["creator"])
    monkeypatch.setattr(service.repo, "list_reviewers", lambda _db, **kw: seen.update(kw) or [])
    assert service.list_reviewers(db, ORG_ID, CREATOR_ID, None, 20) == {"items": [], "next_cursor": None}
    assert seen["org_id"] == ORG_ID
    assert seen["current_user_id"] == CREATOR_ID


def test_last_active_admin_cannot_be_removed(monkeypatch) -> None:
    db = FakeDb()
    monkeypatch.setattr(service.repo, "lock_organisation", lambda _db, _org: True)
    monkeypatch.setattr(service.repo, "active_role", lambda _db, _org, _user: ["admin"])
    monkeypatch.setattr(service.repo, "member_for_update", lambda _db, **kw: {"roles": ["admin"], "status": "active"})
    monkeypatch.setattr(service.repo, "active_admin_count", lambda _db, _org: 1)
    with pytest.raises(ApiError) as blocked:
        service.update_member_roles(db, ORG_ID, ADMIN_ID, ADMIN_ID, ["creator"])
    assert blocked.value.status_code == 409
    assert db.commits == 0


def test_invitation_is_email_bound_and_single_use(monkeypatch) -> None:
    db = FakeDb()
    settings = Settings.model_construct(DATABASE_URL=SecretStr("postgresql://unused"), SESSION_SECRET=SecretStr("test-secret"))
    monkeypatch.setattr(service.repo, "active_role", lambda _db, _org, _user: ["admin"])
    inserted = {}
    monkeypatch.setattr(service.repo, "insert_invitation", lambda _db, **kw: inserted.update(kw))
    created = service.create_invitation(db, settings, ORG_ID, ADMIN_ID, CreateInvitationRequest(email="creator@example.com", role="creator"))
    assert created["invitation_token"].encode() not in inserted["token_hash"]
    assert inserted["email"] == "creator@example.com"
    assert inserted["role"] == "creator"
    assert db.commits == 1

    invitation = {"id": UUID("44444444-4444-4444-8444-444444444444"), "org_id": ORG_ID, "email": "creator@example.com", "roles": ["creator"], "expires_at": datetime.now(timezone.utc) + timedelta(hours=1), "accepted_at": None}
    monkeypatch.setattr(service.repo, "invitation_for_update", lambda _db, _hash: invitation)
    with pytest.raises(ApiError) as wrong_email:
        service.accept_invitation(db, settings, {"id": CREATOR_ID, "email": "other@example.com"}, created["invitation_token"])
    assert wrong_email.value.status_code == 404
    assert db.commits == 1

    invitation["email"] = "other@example.com"
    monkeypatch.setattr(service.repo, "active_role", lambda _db, _org, _user: None)
    accepted = []
    monkeypatch.setattr(service.repo, "insert_membership", lambda _db, **kw: accepted.append(kw))
    monkeypatch.setattr(service.repo, "mark_invitation_accepted", lambda _db, _id: invitation.update(accepted_at=datetime.now(timezone.utc)))
    result = service.accept_invitation(db, settings, {"id": CREATOR_ID, "email": "other@example.com"}, created["invitation_token"])
    assert result == {"org_id": ORG_ID, "roles": ["creator"]}
    assert accepted[0]["org_id"] == ORG_ID
    assert db.commits == 2
    with pytest.raises(ApiError) as replay:
        service.accept_invitation(db, settings, {"id": CREATOR_ID, "email": "other@example.com"}, created["invitation_token"])
    assert replay.value.status_code == 404


def test_org_routes_require_member_access_and_invitation_email(monkeypatch) -> None:
    settings = Settings.model_construct(
        DATABASE_URL=SecretStr("postgresql://unused"),
        SESSION_SECRET=SecretStr("test-secret"),
        APP_ORIGIN="http://localhost:5173",
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: FakeDb()
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.v1.organisations.authentication.current_user", lambda *_args: {"id": ADMIN_ID, "email": "admin@example.com"})
    monkeypatch.setattr(service.repo, "active_role", lambda _db, _org, _user: None)
    client = TestClient(app)
    client.cookies.set("veriloom_session_dev", "opaque-token")

    outside_org = client.get(f"/api/v1/organisations/{ORG_ID}/members")
    assert outside_org.status_code == 404
    assert outside_org.json()["error"]["code"] == "not_found"

    missing_email = client.post(
        f"/api/v1/organisations/{ORG_ID}/invitations",
        headers={"Origin": settings.APP_ORIGIN, "X-CSRF-Token": csrf_token(settings, "opaque-token")},
        json={"role": "creator", "expires_in_hours": 24},
    )
    assert missing_email.status_code == 422
