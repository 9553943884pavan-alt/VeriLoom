"""Source upload lifecycle tests with fake storage and database writes."""

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.sources import get_r2_storage_factory
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.security import csrf_token
from app.integrations.r2 import R2StorageError
from app.db.session import get_session
from app.schemas.sources import SourceUploadMetadata
from app.services import sources
from app.main import create_app


ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
ADMIN_ID = UUID("22222222-2222-4222-8222-222222222222")


class FakeDb:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = fail_commit

    def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeStorage:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.deletes: list[str] = []

    def put_bytes(self, key: str, content: bytes, mime_type: str) -> None:
        self.puts.append((key, content, mime_type))

    def delete(self, key: str) -> None:
        self.deletes.append(key)


def metadata() -> SourceUploadMetadata:
    return SourceUploadMetadata(
        title="Brand guidance", category="guidelines", effective_from=date(2026, 11, 3)
    )


def allow_admin(monkeypatch: pytest.MonkeyPatch, roles: list[str] | None) -> None:
    def require_role(*_args):
        if roles is None:
            raise ApiError(404, "not_found", "missing")
        if "admin" not in roles:
            raise ApiError(403, "forbidden", "denied")
        return roles

    monkeypatch.setattr(sources, "_require_role", require_role)


def test_upload_stores_private_original_and_commits_source_version_and_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, storage = FakeDb(), FakeStorage()
    inserted: dict = {}
    jobs: list[dict] = []
    allow_admin(monkeypatch, ["admin"])
    monkeypatch.setattr(sources.repo, "insert_source_upload", lambda _db, **kwargs: inserted.update(kwargs))
    monkeypatch.setattr(sources, "enqueue_job", lambda _db, **kwargs: jobs.append(kwargs))

    result = sources.upload_source(
        db, storage, org_id=ORG_ID, actor_id=ADMIN_ID, metadata=metadata(),
        filename="guide.md", content=b"Use clear language.",
    )

    assert result["ingestion_status"] == "queued"
    assert inserted["source_id"] == result["source_id"]
    assert inserted["version_id"] == result["version_id"]
    assert inserted["object_key"] == storage.puts[0][0]
    assert storage.puts[0][0].startswith(f"org/{ORG_ID}/sources/")
    assert storage.puts[0][2] == "text/markdown"
    assert jobs == [{"org_id": ORG_ID, "kind": "ingest_source", "resource_id": result["version_id"]}]
    assert db.commits == 1
    assert storage.deletes == []


def test_upload_checks_admin_and_file_before_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = FakeStorage()
    allow_admin(monkeypatch, ["creator"])
    with pytest.raises(ApiError) as denied:
        sources.upload_source(
            FakeDb(), storage, org_id=ORG_ID, actor_id=ADMIN_ID, metadata=metadata(),
            filename="guide.txt", content=b"Guidance",
        )
    assert denied.value.status_code == 403
    assert storage.puts == []

    allow_admin(monkeypatch, ["admin"])
    with pytest.raises(ApiError) as unsupported:
        sources.upload_source(
            FakeDb(), storage, org_id=ORG_ID, actor_id=ADMIN_ID, metadata=metadata(),
            filename="guide.pdf", content=b"Guidance",
        )
    assert unsupported.value.status_code == 415
    assert storage.puts == []


def test_commit_failure_rolls_back_and_removes_r2_orphan(monkeypatch: pytest.MonkeyPatch) -> None:
    db, storage = FakeDb(fail_commit=True), FakeStorage()
    allow_admin(monkeypatch, ["admin"])
    monkeypatch.setattr(sources.repo, "insert_source_upload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sources, "enqueue_job", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="commit failed"):
        sources.upload_source(
            db, storage, org_id=ORG_ID, actor_id=ADMIN_ID, metadata=metadata(),
            filename="guide.txt", content=b"Guidance",
        )
    assert db.rollbacks == 1
    assert len(storage.puts) == len(storage.deletes) == 1
    assert storage.deletes[0] == storage.puts[0][0]


def test_storage_failure_does_not_write_database(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingStorage(FakeStorage):
        def put_bytes(self, key: str, content: bytes, mime_type: str) -> None:
            raise R2StorageError("not stored")

    allow_admin(monkeypatch, ["admin"])
    with pytest.raises(ApiError) as failure:
        sources.upload_source(
            FakeDb(), FailingStorage(), org_id=ORG_ID, actor_id=ADMIN_ID,
            metadata=metadata(), filename="guide.txt", content=b"Guidance",
        )
    assert failure.value.status_code == 503


def test_idempotency_replays_only_same_org_metadata_mime_and_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, storage = FakeDb(), FakeStorage()
    key = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    saved: dict = {}
    allow_admin(monkeypatch, ["admin"])
    monkeypatch.setattr(sources.repo, "insert_source_upload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sources, "enqueue_job", lambda *_args, **_kwargs: None)

    def find_record(_db, *, user_id, endpoint, key):
        return saved.get("record")

    def save_record(_db, *, user_id, org_id, key, request_sha256, source_id, response_json):
        saved["record"] = {
            "org_id": org_id,
            "request_sha256": request_sha256,
            "state": "complete",
            "response_json": response_json,
        }

    monkeypatch.setattr(sources.repo, "get_idempotency_record", find_record)
    monkeypatch.setattr(sources.repo, "insert_idempotency_record", save_record)
    args = dict(
        org_id=ORG_ID, actor_id=ADMIN_ID, metadata=metadata(),
        filename="guide.txt", content=b"Guidance", idempotency_key=key,
    )
    original = sources.upload_source(db, storage, **args)
    replay = sources.upload_source(db, storage, **args)
    assert replay == original
    assert len(storage.puts) == 1

    for changed in (
        {**args, "content": b"Changed guidance"},
        {**args, "filename": "guide.md"},
        {**args, "org_id": UUID("33333333-3333-4333-8333-333333333333")},
    ):
        with pytest.raises(ApiError) as conflict:
            sources.upload_source(db, storage, **changed)
        assert conflict.value.status_code == 409
    assert len(storage.puts) == 1


def test_http_upload_contract_and_openapi_route(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.model_construct(
        DATABASE_URL=SecretStr("postgresql://unused"),
        SESSION_SECRET=SecretStr("test-secret"),
        APP_ORIGIN="http://localhost:5173",
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: FakeDb()
    storage = FakeStorage()
    app.dependency_overrides[get_r2_storage_factory] = lambda: lambda: storage
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.api.v1.organisations.authentication.current_user",
        lambda *_args: {"id": ADMIN_ID, "email": "admin@example.com"},
    )
    monkeypatch.setattr("app.api.v1.sources._require_role", lambda *_args: ["admin"])
    returned = {"source_id": str(ADMIN_ID), "version_id": str(ORG_ID), "ingestion_status": "queued"}
    monkeypatch.setattr(sources, "upload_source", lambda *_args, **_kwargs: returned)
    client = TestClient(app)
    client.cookies.set("veriloom_session_dev", "opaque-token")
    headers = {
        "Origin": settings.APP_ORIGIN,
        "X-CSRF-Token": csrf_token(settings, "opaque-token"),
    }

    path = f"/api/v1/organisations/{ORG_ID}/sources"
    response = client.post(
        path,
        headers=headers,
        data={"title": "Guidance", "category": "guidelines", "effective_from": "2026-11-03"},
        files={"file": ("guide.txt", b"Some guidance", "text/plain")},
    )
    assert response.status_code == 202
    assert response.json() == returned
    assert "/api/v1/organisations/{org_id}/sources" in client.get("/openapi.json").json()["paths"]

    too_large = client.post(
        path,
        headers=headers,
        data={"title": "Guidance", "category": "guidelines", "effective_from": "2026-11-03"},
        files={"file": ("guide.txt", b"x" * (2 * 1024 * 1024 + 1), "text/plain")},
    )
    assert too_large.status_code == 413
