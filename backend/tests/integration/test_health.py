"""Health routes must stay safe even when PostgreSQL is unavailable."""

import psycopg
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.main import create_app


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query: str):
        assert query == "SELECT 1"
        return self

    def fetchone(self):
        return (1,)


def test_health_and_ready_routes(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings.model_construct(
        DATABASE_URL=SecretStr("postgresql://test:test@localhost/test"),
        SESSION_SECRET=SecretStr("test-only-secret"),
        database_connect_timeout_seconds=3,
    )
    monkeypatch.setattr(psycopg, "connect", lambda *_args, **_kwargs: FakeConnection())

    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert "X-Request-ID" in ready.headers
    assert ready.headers["Cache-Control"] == "private, no-store"


def test_readiness_failure_does_not_expose_connection_details(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings.model_construct(
        DATABASE_URL=SecretStr("postgresql://test:private@localhost/test"),
        SESSION_SECRET=SecretStr("test-only-secret"),
        database_connect_timeout_seconds=3,
    )

    def unavailable(*_args, **_kwargs):
        raise psycopg.OperationalError("postgresql://test:private@localhost/test")

    monkeypatch.setattr(psycopg, "connect", unavailable)

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "private" not in response.text
