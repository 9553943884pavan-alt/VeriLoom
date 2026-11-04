"""Browser auth boundary without a live PostgreSQL dependency."""

from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.security import csrf_token, hash_password, verify_password
from app.db.session import get_session
from app.main import create_app
from app.schemas.auth import LoginRequest
from app.services import authentication


USER_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_password_hash_and_csrf_are_distinct() -> None:
    settings = Settings.model_construct(
        DATABASE_URL=SecretStr("postgresql://unused"),
        SESSION_SECRET=SecretStr("local-test-secret"),
    )
    password_hash = hash_password("strong password 123")
    assert password_hash != "strong password 123"
    assert verify_password(password_hash, "strong password 123")
    assert not verify_password(password_hash, "wrong password")
    assert csrf_token(settings, "session-a") != csrf_token(settings, "session-b")


def test_login_stores_only_a_hash_of_the_opaque_token(monkeypatch) -> None:
    settings = Settings.model_construct(
        DATABASE_URL=SecretStr("postgresql://unused"),
        SESSION_SECRET=SecretStr("local-test-secret"),
    )
    user = {"id": USER_ID, "disabled_at": None, "password_hash": hash_password("strong password 123")}
    stored = {}
    monkeypatch.setattr(authentication.identity, "find_user_by_email", lambda _db, _email: user)
    monkeypatch.setattr(authentication.identity, "insert_session", lambda _db, **kwargs: stored.update(kwargs))
    monkeypatch.setattr(authentication.identity, "list_active_memberships", lambda _db, _id: [])
    db = FakeDb()

    body, raw_token = authentication.login(
        db, settings, LoginRequest(email="alice@example.com", password="strong password 123")
    )

    assert body == {"user_id": USER_ID, "memberships": []}
    assert db.commits == 1
    assert stored["user_id"] == USER_ID
    assert isinstance(stored["token_hash"], bytes)
    assert raw_token.encode() not in stored["token_hash"]


def test_browser_auth_flow_enforces_origin_and_csrf(monkeypatch) -> None:
    settings = Settings.model_construct(
        DATABASE_URL=SecretStr("postgresql://unused"),
        SESSION_SECRET=SecretStr("local-test-secret"),
        APP_ORIGIN="http://localhost:5173",
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: object()
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.v1.auth.authentication.register", lambda _db, payload: {"user_id": USER_ID, "email": payload.email, "display_name": payload.display_name})
    monkeypatch.setattr("app.api.v1.auth.authentication.login", lambda _db, _settings, _payload: ({"user_id": USER_ID, "memberships": []}, "opaque-session-token"))
    monkeypatch.setattr("app.api.v1.auth.authentication.current_user", lambda _db, _settings, _token: {"id": USER_ID, "email": "alice@example.com", "display_name": "Alice"})
    monkeypatch.setattr("app.api.v1.auth.list_active_memberships", lambda _db, _user_id: [])
    logged_out = []
    monkeypatch.setattr("app.api.v1.auth.authentication.logout", lambda _db, _settings, token: logged_out.append(token))

    client = TestClient(app)
    wrong_origin = client.post("/api/v1/auth/register", headers={"Origin": "https://evil.example"}, json={"email": "alice@example.com", "password": "strong password 123", "display_name": "Alice"})
    assert wrong_origin.status_code == 403

    registered = client.post("/api/v1/auth/register", headers={"Origin": settings.APP_ORIGIN}, json={"email": "alice@example.com", "password": "strong password 123", "display_name": "Alice"})
    assert registered.status_code == 201
    assert registered.json() == {"user_id": str(USER_ID), "email": "alice@example.com", "display_name": "Alice"}

    logged_in = client.post("/api/v1/auth/login", headers={"Origin": settings.APP_ORIGIN}, json={"email": "alice@example.com", "password": "strong password 123"})
    assert logged_in.status_code == 200
    assert logged_in.json() == {"user_id": str(USER_ID), "memberships": []}
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    assert "SameSite=lax" in logged_in.headers["set-cookie"]
    assert "opaque-session-token" not in logged_in.text

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    csrf = me.json()["csrf_token"]
    assert csrf == csrf_token(settings, "opaque-session-token")

    missing_csrf = client.post("/api/v1/auth/logout", headers={"Origin": settings.APP_ORIGIN}, json={})
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "invalid_csrf"

    logout = client.post("/api/v1/auth/logout", headers={"Origin": settings.APP_ORIGIN, "X-CSRF-Token": csrf}, json={})
    assert logout.status_code == 204
    assert logged_out == ["opaque-session-token"]
    assert "Max-Age=0" in logout.headers["set-cookie"]
