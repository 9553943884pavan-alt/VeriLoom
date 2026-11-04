"""Authorization and read-time status for cited evidence."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.repositories import evidence as repo
from app.services.organisations import _require_role


def get_passage(db: Session, *, org_id: UUID, passage_id: UUID, user_id: UUID) -> dict:
    # Check organisation membership before looking up the passage so callers
    # outside the organisation cannot distinguish existing IDs from missing IDs.
    roles = _require_role(db, org_id, user_id, {"admin", "creator", "reviewer"})
    # Load only evidence that was approved at some point and fully ingested.
    passage = repo.get_passage(db, org_id=org_id, passage_id=passage_id)
    if passage is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")

    if "admin" not in roles and not repo.user_may_read_cited_passage(
        db,
        org_id=org_id,
        passage_id=passage_id,
        user_id=user_id,
        is_creator="creator" in roles,
        is_reviewer="reviewer" in roles,
    ):
        # Hide evidence not connected to a review the caller may access.
        raise ApiError(404, "not_found", "The requested resource was not found.")
    return passage
