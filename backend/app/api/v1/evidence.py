"""Evidence passage inspection endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.organisations import signed_in_user
from app.db.session import get_session
from app.schemas.evidence import EvidencePassage
from app.services import evidence


router = APIRouter(prefix="/organisations", tags=["evidence"])


@router.get("/{org_id}/evidence/{passage_id}", response_model=EvidencePassage)
def get_evidence_passage(
    org_id: UUID,
    passage_id: UUID,
    user: dict = Depends(signed_in_user),
    db: Session = Depends(get_session),
) -> dict:
    return evidence.get_passage(db, org_id=org_id, passage_id=passage_id, user_id=user["id"])
