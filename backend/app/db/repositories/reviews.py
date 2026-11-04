"""Tenant-scoped review, discussion, and human decision SQL."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_review(db: Session, *, org_id: UUID, review_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""SELECT r.id AS review_id,x.id AS draft_id,v.id AS revision_id,v.revision_number,
        r.reviewer_user_id AS assigned_reviewer_user_id,r.state,r.coverage,r.coverage_reason,r.review_version,
        r.reviewed_against_corpus_version,r.error_code,r.created_at,r.completed_at,x.creator_user_id,x.current_revision_id
        FROM reviews r JOIN draft_revisions v ON v.org_id=r.org_id AND v.id=r.revision_id
        JOIN drafts x ON x.org_id=v.org_id AND x.id=v.draft_id
        WHERE r.org_id=:org_id AND r.id=:review_id"""), {"org_id": org_id, "review_id": review_id}).mappings().first()
    return dict(row) if row else None


def findings(db: Session, *, org_id: UUID, review_id: UUID) -> list[dict[str, Any]]:
    rows = db.execute(text("""SELECT f.id AS finding_id,f.category,f.severity,f.evidence_status,
        f.anchor_start,f.anchor_end,f.quote,f.explanation,f.suggested_edit,
        COALESCE(jsonb_agg(jsonb_build_object('passage_id',c.passage_id,'source_ref',c.source_ref,
          'source_id',p.source_id,'source_version',c.source_version,'title',c.source_title,
          'excerpt',c.excerpt,'effective_from',c.effective_from,'current_status',
          CASE WHEN s.archived_at IS NOT NULL OR v.approval_status <> 'approved' THEN 'archived'
               WHEN v.effective_to IS NOT NULL AND v.effective_to <= timezone(o.time_zone,now())::date THEN 'expired'
               ELSE 'approved' END) ORDER BY c.sort_order)
          FILTER (WHERE c.id IS NOT NULL),'[]'::jsonb) AS citations
        FROM findings f JOIN organisations o ON o.id=f.org_id
        LEFT JOIN finding_citations c ON c.org_id=f.org_id AND c.finding_id=f.id
        LEFT JOIN source_passages p ON p.org_id=c.org_id AND p.id=c.passage_id
        LEFT JOIN source_versions v ON v.org_id=p.org_id AND v.id=p.version_id
        LEFT JOIN sources s ON s.org_id=p.org_id AND s.id=p.source_id
        WHERE f.org_id=:org_id AND f.review_id=:review_id GROUP BY f.id,o.time_zone ORDER BY f.sort_order"""), {"org_id": org_id, "review_id": review_id}).mappings().all()
    return [dict(row) for row in rows]


def latest_decision(db: Session, *, org_id: UUID, review_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""SELECT id AS decision_id,action,comment,override_reason,
        actor_user_id AS decided_by_user_id,created_at AS decided_at
        FROM decisions WHERE org_id=:org_id AND review_id=:review_id
        ORDER BY created_at DESC,id DESC LIMIT 1"""),
        {"org_id": org_id, "review_id": review_id}).mappings().first()
    return dict(row) if row is not None else None


def insert_comment(db: Session, *, org_id: UUID, review_id: UUID, revision_id: UUID, actor_id: UUID, body: str) -> dict[str, Any]:
    row = db.execute(text("""INSERT INTO comments(org_id,review_id,revision_id,actor_user_id,body) VALUES (:org_id,:review_id,:revision_id,:actor_id,:body) RETURNING id AS comment_id,revision_id,body,actor_user_id,created_at"""), {"org_id":org_id,"review_id":review_id,"revision_id":revision_id,"actor_id":actor_id,"body":body}).mappings().one()
    return dict(row)


def list_comments(db: Session, *, org_id: UUID, review_id: UUID, after: tuple[Any, UUID] | None, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""SELECT id AS comment_id,revision_id,body,actor_user_id,created_at FROM comments WHERE org_id=:org_id AND review_id=:review_id AND (CAST(:after_at AS timestamptz) IS NULL OR (created_at,id)>(CAST(:after_at AS timestamptz),CAST(:after_id AS uuid))) ORDER BY created_at,id LIMIT :limit"""), {"org_id":org_id,"review_id":review_id,"after_at":after[0] if after else None,"after_id":after[1] if after else None,"limit":limit}).mappings().all()
    return [dict(row) for row in rows]


def insert_question(db: Session, *, org_id: UUID, review_id: UUID, actor_id: UUID,
                    finding_id: UUID | None, question: str) -> UUID:
    return db.execute(text("""INSERT INTO chat_turns(org_id,review_id,actor_user_id,finding_id,question)
        VALUES (:org_id,:review_id,:actor_id,:finding_id,:question) RETURNING id"""),
        {"org_id":org_id,"review_id":review_id,"actor_id":actor_id,
         "finding_id":finding_id,"question":question}).scalar_one()


def list_questions(db: Session, *, org_id: UUID, review_id: UUID, after: tuple[Any, UUID] | None,
                   limit: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""SELECT id AS turn_id,finding_id,question,rewritten_query,state,route,
        answer_status,answer,created_at,answered_at FROM chat_turns WHERE org_id=:org_id AND review_id=:review_id
        AND (CAST(:after_at AS timestamptz) IS NULL OR (created_at,id)>(CAST(:after_at AS timestamptz),CAST(:after_id AS uuid)))
        ORDER BY created_at,id LIMIT :limit"""),
        {"org_id":org_id,"review_id":review_id,"after_at":after[0] if after else None,"after_id":after[1] if after else None,"limit":limit}).mappings().all()
    return [dict(row) for row in rows]


def get_question(db: Session, *, org_id: UUID, review_id: UUID, turn_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""SELECT t.id AS turn_id,t.finding_id,t.question,t.rewritten_query,t.state,t.route,
        t.answer_status,t.answer,t.created_at,t.answered_at FROM chat_turns t
        WHERE t.org_id=:org_id AND t.review_id=:review_id AND t.id=:turn_id"""),
        {"org_id":org_id,"review_id":review_id,"turn_id":turn_id}).mappings().first()
    if row is None:
        return None
    result = dict(row)
    citations = db.execute(text("""SELECT c.passage_id,c.source_ref,p.source_id,c.source_version,c.source_title AS title,
        c.excerpt,c.effective_from,CASE WHEN s.archived_at IS NOT NULL OR v.approval_status<>'approved' THEN 'archived'
          WHEN v.effective_to IS NOT NULL AND v.effective_to<=timezone(o.time_zone,now())::date THEN 'expired' ELSE 'approved' END AS current_status
        FROM chat_citations c JOIN source_passages p ON p.org_id=c.org_id AND p.id=c.passage_id
        JOIN source_versions v ON v.org_id=p.org_id AND v.id=p.version_id
        JOIN sources s ON s.org_id=p.org_id AND s.id=p.source_id
        JOIN organisations o ON o.id=c.org_id
        WHERE c.org_id=:org_id AND c.turn_id=:turn_id ORDER BY c.sort_order"""),
        {"org_id":org_id,"turn_id":turn_id}).mappings().all()
    result["citations"] = [dict(c) for c in citations]
    return result


def finding_belongs_to_review(db: Session, *, org_id: UUID, review_id: UUID, finding_id: UUID) -> bool:
    return bool(db.execute(text("SELECT EXISTS(SELECT 1 FROM findings WHERE org_id=:org_id AND review_id=:review_id AND id=:finding_id)"),
        {"org_id":org_id,"review_id":review_id,"finding_id":finding_id}).scalar_one())


def current_review_retry(db: Session, *, org_id: UUID, review_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""SELECT r.id AS review_id,r.revision_id,r.state,d.id AS draft_id,
        d.current_revision_id,d.creator_user_id,r.reviewer_user_id
        FROM reviews r JOIN draft_revisions v ON v.org_id=r.org_id AND v.id=r.revision_id
        JOIN drafts d ON d.org_id=v.org_id AND d.id=v.draft_id
        WHERE r.org_id=:org_id AND r.id=:review_id FOR UPDATE OF r,d"""),
        {"org_id":org_id,"review_id":review_id}).mappings().first()
    return dict(row) if row else None


def lock_review_decision(db: Session, *, org_id: UUID, review_id: UUID) -> dict[str, Any] | None:
    row = db.execute(text("""SELECT r.id AS review_id,r.revision_id,r.reviewer_user_id,r.state,r.review_version,
        d.id AS draft_id,d.creator_user_id,d.current_revision_id FROM reviews r
        JOIN draft_revisions v ON v.org_id=r.org_id AND v.id=r.revision_id
        JOIN drafts d ON d.org_id=v.org_id AND d.id=v.draft_id
        WHERE r.org_id=:org_id AND r.id=:review_id FOR UPDATE OF r,d"""), {"org_id":org_id,"review_id":review_id}).mappings().first()
    return dict(row) if row else None


def has_override_trigger(db: Session, *, org_id: UUID, review_id: UUID) -> bool:
    return bool(db.execute(text("""SELECT EXISTS(SELECT 1 FROM findings WHERE org_id=:org_id AND review_id=:review_id AND severity='high') OR EXISTS(SELECT 1 FROM reviews WHERE org_id=:org_id AND id=:review_id AND coverage='insufficient')"""), {"org_id":org_id,"review_id":review_id}).scalar_one())


def insert_decision(db: Session, *, org_id: UUID, review_id: UUID, draft_id: UUID, actor_id: UUID, action: str, comment: str | None, override_reason: str | None, expected: int, review_state: str, draft_state: str) -> dict[str, Any]:
    row = db.execute(text("""INSERT INTO decisions(org_id,review_id,actor_user_id,action,comment,override_reason,expected_review_version) VALUES (:org_id,:review_id,:actor_id,:action,:comment,:override_reason,:expected) RETURNING id AS decision_id,review_id,action,comment,override_reason,actor_user_id AS decided_by_user_id,created_at AS decided_at"""), {"org_id":org_id,"review_id":review_id,"actor_id":actor_id,"action":action,"comment":comment,"override_reason":override_reason,"expected":expected}).mappings().one()
    db.execute(text("""UPDATE reviews SET state=:review_state,review_version=review_version+1,updated_at=now() WHERE org_id=:org_id AND id=:review_id"""), {"review_state":review_state,"org_id":org_id,"review_id":review_id})
    db.execute(text("""UPDATE drafts SET state=:draft_state,updated_at=now() WHERE org_id=:org_id AND id=:draft_id"""), {"draft_state":draft_state,"org_id":org_id,"draft_id":draft_id})
    return dict(row)
