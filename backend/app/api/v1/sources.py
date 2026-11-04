"""Administrator routes for uploading organisation guidance."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.organisations import signed_in_user
from app.core.errors import ApiError
from app.db.session import get_session
from app.integrations.r2 import R2Storage
from app.schemas.sources import SourceApprovalRequest, SourceApprovalResponse, SourceUploadAccepted, SourceUploadMetadata
from app.services import sources
from app.services.organisations import _require_role


router = APIRouter(prefix="/organisations", tags=["sources"])
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def get_r2_storage_factory() -> Callable[[], R2Storage]:
    """Return a lazy adapter factory so authorization precedes configuration access."""
    return R2Storage


@router.post(
    "/{org_id}/sources",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SourceUploadAccepted,
)
def upload_source(
    org_id: UUID,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    category: Annotated[str, Form()],
    effective_from: Annotated[str, Form()],
    effective_to: Annotated[str | None, Form()] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
    storage_factory: Callable[[], R2Storage] = Depends(get_r2_storage_factory),
) -> dict:
    try:
        metadata = SourceUploadMetadata(
            title=title, category=category,
            effective_from=effective_from, effective_to=effective_to,
        )
    except ValidationError as exc:
        fields = [{"field": ".".join(map(str, item["loc"])), "type": item["type"]} for item in exc.errors()]
        raise ApiError(422, "validation_error", "The request is invalid.", {"fields": fields}) from exc

    key: UUID | None = None
    if idempotency_header is not None:
        try:
            key = UUID(idempotency_header)
        except ValueError as exc:
            raise ApiError(422, "invalid_idempotency_key", "Idempotency-Key must be a UUID.") from exc

    _require_role(db, org_id, user["id"], {"admin"})
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "file_too_large", "The file exceeds the 2 MiB limit.")
    return sources.upload_source(
        db, storage_factory, org_id=org_id, actor_id=user["id"], metadata=metadata,
        filename=file.filename or "", content=content, idempotency_key=key,
    )


@router.post(
    "/{org_id}/sources/{source_id}/versions/{version_id}/approve",
    response_model=SourceApprovalResponse,
)
def approve_source_version(
    org_id: UUID,
    source_id: UUID,
    version_id: UUID,
    payload: SourceApprovalRequest,
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
) -> dict:
    return sources.approve_source_version(
        db, org_id=org_id, source_id=source_id, version_id=version_id,
        actor_id=user["id"], request=payload,
    )
