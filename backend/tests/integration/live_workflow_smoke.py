"""Opt-in live API/worker smoke against the disposable VeriLoom test database.

Set VERILOOM_LIVE_TEST_DATABASE to a migrated disposable database whose name
starts with ``veriloom_live_test_``. The script never uses the configured
application database and prints no credentials or content.
"""

from __future__ import annotations

import os
import secrets
import time
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy.engine import make_url

from app.core.config import get_settings

TEST_DATABASE = os.environ.get("VERILOOM_LIVE_TEST_DATABASE", "")
base_url = make_url(get_settings().DATABASE_URL.get_secret_value())
if not TEST_DATABASE.startswith("veriloom_live_test_") or base_url.database == TEST_DATABASE:
    raise RuntimeError("Specify a separate migrated VERILOOM_LIVE_TEST_DATABASE")
os.environ["DATABASE_URL"] = base_url.set(database=TEST_DATABASE).render_as_string(hide_password=False)
get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.session import get_engine  # noqa: E402
from app.jobs.worker import run_once  # noqa: E402
from app.main import app  # noqa: E402


def expect(response, status: int, label: str) -> dict:
    if response.status_code != status:
        try:
            body = response.json()
            code = body.get("error", {}).get("code", "unknown")
        except ValueError:
            code = "non_json_response"
        raise AssertionError(f"{label}: HTTP {response.status_code}, code={code}, expected={status}")
    return response.json() if response.content else {}


def unsafe(client: TestClient, csrf: str | None = None, *, key: bool = False) -> dict:
    headers = {"Origin": get_settings().APP_ORIGIN}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if key:
        headers["Idempotency-Key"] = str(uuid4())
    return headers


def actor(name: str, suffix: str, password: str) -> tuple[TestClient, str, str]:
    client = TestClient(app)
    email = f"{name}-{suffix}@example.com"
    created = expect(client.post("/api/v1/auth/register", json={
        "email": email, "password": password, "display_name": name.title(),
    }, headers=unsafe(client)), 201, f"register {name}")
    expect(client.post("/api/v1/auth/login", json={
        "email": email, "password": password,
    }, headers=unsafe(client)), 200, f"login {name}")
    me = expect(client.get("/api/v1/auth/me"), 200, f"me {name}")
    assert me["user_id"] == created["user_id"]
    return client, me["csrf_token"], created["user_id"]


def work() -> None:
    suffix = uuid4().hex[:10]
    password = secrets.token_urlsafe(24)
    admin, ac, _ = actor("admin", suffix, password)
    creator, cc, creator_id = actor("creator", suffix, password)
    reviewer, rc, reviewer_id = actor("reviewer", suffix, password)
    print("PASS auth: three registered sessions and CSRF tokens", flush=True)

    org = expect(admin.post("/api/v1/organisations", json={
        "name": f"Live Test {suffix}", "time_zone": "UTC",
    }, headers=unsafe(admin, ac)), 201, "create organisation")
    org_id = org["org_id"]
    for name, target, token in (
        ("creator", creator, cc), ("reviewer", reviewer, rc)
    ):
        invitation = expect(admin.post(f"/api/v1/organisations/{org_id}/invitations", json={
            "email": f"{name}-{suffix}@example.com", "role": name,
        }, headers=unsafe(admin, ac)), 201, f"invite {name}")
        expect(target.post("/api/v1/invitations/accept", json={
            "invitation_token": invitation["invitation_token"],
        }, headers=unsafe(target, token)), 200, f"accept {name}")
    print("PASS organisation: invitations and roles", flush=True)

    # Exercise both Origin and CSRF at the trust boundary before the upload.
    denied = admin.post("/api/v1/organisations", json={"name": "Denied"}, headers={
        "Origin": "https://invalid.example", "X-CSRF-Token": ac,
    })
    assert denied.status_code == 403
    denied = admin.post("/api/v1/organisations", json={"name": "Denied"}, headers={
        "Origin": get_settings().APP_ORIGIN,
    })
    assert denied.status_code == 403
    print("PASS security: Origin and CSRF rejection", flush=True)

    guide = b"# Coffee claims\nDo not describe our coffee as best. Use a plain, friendly tone.\n"
    uploaded = expect(admin.post(f"/api/v1/organisations/{org_id}/sources", data={
        "title": "Coffee claims", "category": "guidelines",
        "effective_from": (date.today() - timedelta(days=1)).isoformat(),
    }, files={"file": ("coffee_claims.md", guide, "text/markdown")},
        headers=unsafe(admin, ac, key=True)), 202, "upload guidance")
    source_id, version_id = uploaded["source_id"], uploaded["version_id"]
    version_path = f"/api/v1/organisations/{org_id}/sources/{source_id}/versions/{version_id}"
    source = expect(admin.get(version_path), 200, "source queued status")
    assert source["ingestion_status"] == "queued"
    def process_until(client: TestClient, path: str, field: str, expected: str) -> dict:
        for _ in range(15):
            state = expect(client.get(path), 200, f"poll {field}")
            if state[field] == expected:
                return state
            if state[field] == "failed":
                raise AssertionError(f"{field} failed")
            if not run_once(sessionmaker(bind=get_engine()), worker_id=f"live-smoke-{suffix}"):
                time.sleep(2)
        raise AssertionError(f"{field} did not reach {expected}")

    source = process_until(admin, version_path, "ingestion_status", "ready")
    assert source["ingestion_status"] == "ready", source
    assert source["passage_count"] >= 1
    original = admin.get(version_path + "/original")
    assert original.status_code == 200 and original.content == guide
    assert creator.get(version_path + "/original").status_code == 403
    listing = expect(admin.get(f"/api/v1/organisations/{org_id}/sources"), 200, "source list")
    assert any(item["source_id"] == source_id for item in listing["items"])
    approved = expect(admin.post(version_path + "/approve", json={
        "expected_corpus_version": 0,
    }, headers=unsafe(admin, ac)), 200, "approve guidance")
    assert approved["corpus_version"] == 1
    print("PASS guidance: R2 upload, ingestion, E5 vector, approval, list/detail", flush=True)

    draft = expect(creator.post(f"/api/v1/organisations/{org_id}/drafts", json={
        "title": "Coffee social post", "channel": "social_post", "content_format": "text",
        "content": "Our coffee is the best.",
    }, headers=unsafe(creator, cc)), 201, "create draft")
    draft_id = draft["draft_id"]
    submit_path = f"/api/v1/organisations/{org_id}/drafts/{draft_id}/submissions"

    def submit(version: int) -> dict:
        return expect(creator.post(submit_path, json={
            "expected_working_copy_version": version, "reviewer_user_id": reviewer_id,
        }, headers=unsafe(creator, cc, key=True)), 202, "submit draft")

    def complete_review(submission: dict) -> dict:
        return process_until(reviewer, submission["status_url"], "state", "ready_for_review")

    first = submit(1)
    review1 = complete_review(first)
    assert review1["reviewed_against_corpus_version"] == 1
    assert reviewer.get(f"/api/v1/organisations/{org_id}/drafts/{draft_id}").status_code == 200
    assert admin.get(first["status_url"]).status_code == 403 or admin.get(first["status_url"]).status_code == 404
    print("PASS initial review: submission, retrieval, Sarvam, validated review", flush=True)

    question = expect(reviewer.post(f"/api/v1/organisations/{org_id}/reviews/{first['review_id']}/questions", json={
        "revision_id": first["revision_id"],
        "question": "Does the approved guidance prohibit describing our coffee as best?",
    }, headers=unsafe(reviewer, rc, key=True)), 202, "ask follow-up")
    turn = process_until(reviewer, question["status_url"], "state", "completed")
    assert turn["answer_status"] == "answered" and len(turn["citations"]) >= 1
    print(f"PASS follow-up: route={turn['route']}, status={turn['answer_status']}", flush=True)

    decision_path = f"/api/v1/organisations/{org_id}/reviews/{first['review_id']}/decisions"
    requested = expect(reviewer.post(decision_path, json={
        "expected_revision_id": first["revision_id"],
        "expected_review_version": review1["review_version"],
        "action": "request_changes", "comment": "Please remove the best claim.",
    }, headers=unsafe(reviewer, rc, key=True)), 201, "request changes")
    assert requested["draft_state"] == "changes_requested"
    updated = expect(creator.patch(f"/api/v1/organisations/{org_id}/drafts/{draft_id}", json={
        "expected_working_copy_version": 1, "content": "Our coffee is freshly roasted.",
    }, headers=unsafe(creator, cc)), 200, "revise draft")
    second = submit(updated["working_copy_version"])
    assert second["revision_number"] == 2
    review2 = complete_review(second)
    approved_decision = expect(reviewer.post(
        f"/api/v1/organisations/{org_id}/reviews/{second['review_id']}/decisions", json={
            "expected_revision_id": second["revision_id"],
            "expected_review_version": review2["review_version"],
            "action": "approve", "override_reason": "Human reviewer checked this revised content and approves it.",
        }, headers=unsafe(reviewer, rc, key=True)), 201, "approve revision")
    assert approved_decision["draft_state"] == "approved"
    old = expect(creator.get(f"/api/v1/organisations/{org_id}/drafts/{draft_id}/revisions/1"), 200, "old revision")
    assert old["revision_number"] == 1
    print("PASS human loop: request changes, edit, resubmit, approve, immutable revision", flush=True)


if __name__ == "__main__":
    work()
