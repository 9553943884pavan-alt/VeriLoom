"""Malformed public pagination cursors must return a client error."""

import pytest
from fastapi.testclient import TestClient
from uuid import UUID

from app.api.v1.organisations import signed_in_user
from app.core.errors import ApiError
from app.db.session import get_session
from app.main import create_app
from app.services import drafts, organisations, reviews


@pytest.mark.parametrize(
    "decode",
    [organisations._decode_cursor, drafts._cursor, reviews._cursor],
)
def test_malformed_base64_cursor_is_a_validation_error(decode) -> None:
    with pytest.raises(ApiError) as raised:
        decode("a")

    assert raised.value.status_code == 422
    assert raised.value.code == "invalid_cursor"


def test_draft_list_uses_contract_status_query_parameter(monkeypatch) -> None:
    org_id = UUID("11111111-1111-4111-8111-111111111111")
    user_id = UUID("22222222-2222-4222-8222-222222222222")
    app = create_app()
    app.dependency_overrides[signed_in_user] = lambda: {"id": user_id}
    app.dependency_overrides[get_session] = lambda: object()
    seen = {}

    def listing(_db, **kwargs):
        seen.update(kwargs)
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr("app.api.v1.drafts.drafts.listing", listing)
    response = TestClient(app).get(f"/api/v1/organisations/{org_id}/drafts?view=mine&status=changes_requested")

    assert response.status_code == 200
    assert seen["state"] == "changes_requested"
    assert seen["org_id"] == org_id
    assert seen["user_id"] == user_id
