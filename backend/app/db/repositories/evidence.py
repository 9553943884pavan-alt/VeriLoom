"""Tenant-scoped reads for immutable evidence passages."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_passage(db: Session, *, org_id: UUID, passage_id: UUID) -> dict[str, Any] | None:
    """Return an approved or historical passage, never a pending upload."""
    row = db.execute(
        text("""
            SELECT p.id AS passage_id, p.source_ref, p.source_id,
                   v.version_label AS source_version, p.title,
                   p.text_content AS excerpt, v.effective_from,
                   CASE
                     WHEN s.archived_at IS NOT NULL OR v.archived_at IS NOT NULL
                          OR v.approval_status = 'archived' THEN 'archived'
                     WHEN v.effective_from > timezone(o.time_zone, now())::date
                          OR (v.effective_to IS NOT NULL
                              AND v.effective_to <= timezone(o.time_zone, now())::date)
                       THEN 'expired'
                     ELSE 'approved'
                   END AS current_status
            FROM source_passages AS p
            JOIN source_versions AS v
              ON v.org_id = p.org_id AND v.source_id = p.source_id AND v.id = p.version_id
            JOIN sources AS s ON s.org_id = p.org_id AND s.id = p.source_id
            JOIN organisations AS o ON o.id = p.org_id
            WHERE p.org_id = :org_id AND p.id = :passage_id
              AND v.ingestion_status = 'ready'
              AND v.approval_status IN ('approved', 'archived')
        """),
        {"org_id": org_id, "passage_id": passage_id},
    ).mappings().first()
    return dict(row) if row else None


def user_may_read_cited_passage(
    db: Session,
    *,
    org_id: UUID,
    passage_id: UUID,
    user_id: UUID,
    is_creator: bool,
    is_reviewer: bool,
) -> bool:
    """Check that an accessible review or chat turn cites this passage."""
    return bool(
        db.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM finding_citations AS c
                    JOIN findings AS f
                      ON f.org_id = c.org_id AND f.id = c.finding_id
                    JOIN reviews AS r
                      ON r.org_id = f.org_id AND r.id = f.review_id
                    JOIN draft_revisions AS v
                      ON v.org_id = r.org_id AND v.id = r.revision_id
                    JOIN drafts AS d
                      ON d.org_id = v.org_id AND d.id = v.draft_id
                    WHERE c.org_id = :org_id AND c.passage_id = :passage_id
                      AND ((:is_creator AND d.creator_user_id = :user_id)
                           OR (:is_reviewer AND r.reviewer_user_id = :user_id))
                ) OR EXISTS (
                    SELECT 1
                    FROM chat_citations AS c
                    JOIN chat_turns AS t
                      ON t.org_id = c.org_id AND t.id = c.turn_id
                    JOIN reviews AS r
                      ON r.org_id = t.org_id AND r.id = t.review_id
                    JOIN draft_revisions AS v
                      ON v.org_id = r.org_id AND v.id = r.revision_id
                    JOIN drafts AS d
                      ON d.org_id = v.org_id AND d.id = v.draft_id
                    WHERE c.org_id = :org_id AND c.passage_id = :passage_id
                      AND ((:is_creator AND d.creator_user_id = :user_id)
                           OR (:is_reviewer AND r.reviewer_user_id = :user_id))
                )
            """),
            {
                "org_id": org_id,
                "passage_id": passage_id,
                "user_id": user_id,
                "is_creator": is_creator,
                "is_reviewer": is_reviewer,
            },
        ).scalar_one()
    )
