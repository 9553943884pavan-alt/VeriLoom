"""Public evidence passage response schemas."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvidencePassage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    passage_id: UUID
    source_ref: str
    source_id: UUID
    source_version: str
    title: str
    excerpt: str
    effective_from: date
    current_status: Literal["approved", "archived", "expired"]
