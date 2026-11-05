"""Answer an assigned reviewer's follow-up from current approved evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.integrations.failover import SarvamFirstChat, build_worker_chat, completed_provenance
from app.integrations.openai_compatible import OpenAICompatibleError
from app.integrations.sarvam import SarvamChat, SarvamError
from app.jobs.handlers.review_revision import SYSTEM_PROMPT
from app.rag.answer_validation import RewriteCandidate, validate_answer
from app.rag.retrieval.followup import route_followup
from app.rag.retrieval.hybrid import CorpusChanged, retrieve_evidence

ROUTER_VERSION = "followup_router/1.1.0"
REWRITE_VERSION = "rewrite_followup/2.0.0"
ANSWER_VERSION = "grounded_answer/2.0.1"

REWRITE_PROMPT = """TASK: Rewrite the raw reviewer question as one standalone search query, or ask for clarification. Return exactly the three rewrite_followup_v2 JSON fields. Use the selected finding and at most four recent turns only to resolve references; prior answers are not evidence. Preserve names, numbers, dates, quotation marks, comparisons, and negation. Do not invent a source, fact, policy, conclusion, or reviewer intent. If the referent remains ambiguous, set needs_clarification=true, standalone_query=null and ask one short clarification. Otherwise set needs_clarification=false, provide one concise standalone_query, and set clarification_question=null. Do not answer the brand question."""

ANSWER_PROMPT = """TASK: Answer the raw reviewer question about the immutable revision using current_evidence only. Return exactly the grounded_answer_v2 JSON fields. Selected finding and recent turns may resolve references but are not brand authority. Cite 1-3 distinct current passage IDs with exact supporting quotes only when the passages directly establish the narrow answer. If the question asks whether guidance prohibits or requires a specific phrase or action, and a passage explicitly states that rule for the same subject, answer that narrow question with a citation. Do not require an approved alternative or broader policy when the reviewer did not ask for one. If the referent is ambiguous, use clarification_needed with one question and evidence=[]. If current evidence is missing, conflicting, or too weak for the actual question, use insufficient_evidence and evidence=[]. Never approve content, apply an edit, claim to inspect images, or use model memory to fill gaps. Keep the answer at most 900 characters."""

REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["needs_clarification", "standalone_query", "clarification_question"],
    "properties": {
        "needs_clarification": {"type": "boolean"},
        "standalone_query": {"type": ["string", "null"], "maxLength": 300},
        "clarification_question": {"type": ["string", "null"], "maxLength": 200},
    },
}
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["answer_status", "answer", "evidence"],
    "properties": {
        "answer_status": {"type": "string", "enum": ["answered", "clarification_needed", "insufficient_evidence"]},
        "answer": {"type": "string", "minLength": 1, "maxLength": 900},
        "evidence": {"type": "array", "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["passage_id", "supporting_quote"],
            "properties": {
                "passage_id": {"type": "string", "format": "uuid"},
                "supporting_quote": {"type": "string", "minLength": 1, "maxLength": 300},
            },
        }},
    },
}


class AnswerChat(Protocol):
    @property
    def model(self) -> str: ...

    def complete_json(self, messages: Sequence[Mapping[str, str]], *, schema_name: str,
                      schema: Mapping[str, Any], max_tokens: int = 2048) -> dict[str, Any]: ...


class FollowupEmbedder(Protocol):
    model_id: str

    def count_tokens(self, full_input: str) -> int: ...

    def embed_query(self, query: str) -> list[float]: ...


class AnswerFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _current_finding_evidence(db: Session, *, org_id: UUID, finding_id: UUID | None,
                              as_of: date, model_id: str) -> list[dict[str, Any]]:
    if finding_id is None:
        return []
    rows = db.execute(text("""
        SELECT p.id AS passage_id,p.source_ref,p.source_id,p.version_id,
               v.version_label AS source_version,p.title,p.text_content AS text,
               v.effective_from
        FROM finding_citations c
        JOIN source_passages p ON p.org_id=c.org_id AND p.id=c.passage_id
        JOIN source_versions v ON v.org_id=p.org_id AND v.id=p.version_id
        JOIN sources s ON s.org_id=p.org_id AND s.id=p.source_id
        WHERE c.org_id=:org_id AND c.finding_id=:finding_id
          AND v.approval_status='approved' AND v.ingestion_status='ready'
          AND s.archived_at IS NULL AND v.effective_from<=:as_of
          AND (v.effective_to IS NULL OR :as_of<v.effective_to)
          AND p.embedding_model=:model_id
        ORDER BY c.sort_order LIMIT 8
    """), {"org_id": org_id, "finding_id": finding_id,
           "as_of": as_of, "model_id": model_id}).mappings().all()
    return [dict(row) for row in rows]


def _recent_turns(db: Session, *, org_id: UUID, review_id: UUID,
                  created_at: datetime, turn_id: UUID) -> list[dict[str, str]]:
    rows = db.execute(text("""
        SELECT question,answer FROM chat_turns
        WHERE org_id=:org_id AND review_id=:review_id AND state='completed'
          AND (created_at,id)<(:created_at,:turn_id)
        ORDER BY created_at DESC,id DESC LIMIT 4
    """), {"org_id": org_id, "review_id": review_id,
           "created_at": created_at, "turn_id": turn_id}).mappings().all()
    return [{"question": row["question"][:500], "answer": row["answer"][:900]}
            for row in reversed(rows)]


def _preserves_terms(question: str, rewrite: str) -> bool:
    """Reject obvious loss of quoted terms, numbers, or explicit negation."""
    pattern = r'["\u201c]([^"\u201d]+)["\u201d]|\b\d+(?:[.,]\d+)*\b|\b(?:not|never|no|without)\b'
    terms = (match.group(1) or match.group(0) for match in re.finditer(pattern, question, re.I))
    for term in terms:
        if term and term.casefold() not in rewrite.casefold():
            return False
    return True


def _select(db: Session, *, org_id: UUID, turn_id: UUID, embedder: FollowupEmbedder,
            chat: AnswerChat, today: date | None) -> tuple[dict[str, Any], list[dict[str, Any]],
                                                         list[dict[str, str]], str, str | None, str | None]:
    row = db.execute(text("""
        SELECT t.id,t.review_id,t.finding_id,t.question,t.state,t.created_at,
               r.revision_id,r.state AS review_state,d.current_revision_id,
               v.content_nfc,v.channel,o.corpus_version,o.time_zone AS timezone
        FROM chat_turns t
        JOIN reviews r ON r.org_id=t.org_id AND r.id=t.review_id
        JOIN draft_revisions v ON v.org_id=r.org_id AND v.id=r.revision_id
        JOIN drafts d ON d.org_id=v.org_id AND d.id=v.draft_id
        JOIN organisations o ON o.id=t.org_id
        WHERE t.org_id=:org_id AND t.id=:turn_id FOR UPDATE OF t
    """), {"org_id": org_id, "turn_id": turn_id}).mappings().first()
    if row is None:
        raise AnswerFailure("question_not_found")
    turn = dict(row)
    if turn["state"] == "completed":
        return turn, [], [], "completed", None, None
    if (turn["state"] not in {"queued", "processing"}
            or turn["review_state"] != "ready_for_review"
            or turn["revision_id"] != turn["current_revision_id"]):
        raise AnswerFailure("question_not_processable")
    try:
        as_of = today or datetime.now(ZoneInfo(turn["timezone"])).date()
    except (ZoneInfoNotFoundError, TypeError, KeyError):
        raise AnswerFailure("invalid_organisation_timezone") from None
    recent = _recent_turns(db, org_id=org_id, review_id=turn["review_id"],
                           created_at=turn["created_at"], turn_id=turn_id)
    finding = None
    if turn["finding_id"]:
        finding = db.execute(text("""
            SELECT quote,explanation FROM findings
            WHERE org_id=:org_id AND review_id=:review_id AND id=:finding_id
        """), {"org_id": org_id, "review_id": turn["review_id"],
               "finding_id": turn["finding_id"]}).mappings().first()
        if finding is None:
            raise AnswerFailure("finding_not_found")
    reused = _current_finding_evidence(db, org_id=org_id, finding_id=turn["finding_id"],
                                       as_of=as_of, model_id=embedder.model_id)
    if reused:
        citation_count = db.execute(text("""
            SELECT count(*) FROM finding_citations
            WHERE org_id=:org_id AND finding_id=:finding_id
        """), {"org_id": org_id, "finding_id": turn["finding_id"]}).scalar_one()
        if citation_count != len(reused):
            reused = []
    choice = route_followup(turn["question"], has_finding=finding is not None,
                            current_finding_evidence=bool(reused), has_recent_context=bool(recent))
    route = choice.route
    query: str | None = None
    clarification: str | None = None
    rewrite_provider_id: str | None = None
    rewrite_model_id: str | None = None
    context = {"question": turn["question"], "finding": dict(finding) if finding else None,
               "recent_turns": recent}
    if route == "rewrite_retrieve":
        try:
            rewritten = RewriteCandidate.model_validate(chat.complete_json(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "developer", "content": REWRITE_PROMPT},
                 {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)}],
                schema_name="veriloom_rewrite_followup_v2", schema=REWRITE_SCHEMA, max_tokens=4096,
            ))
            rewrite_provider_id, rewrite_model_id = completed_provenance(chat)
        except (SarvamError, OpenAICompatibleError) as exc:
            raise AnswerFailure("rewrite_provider_error", retryable=True) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise AnswerFailure("invalid_rewrite_output") from exc
        if rewritten.needs_clarification:
            route = "clarify"
            clarification = rewritten.clarification_question
        else:
            query = rewritten.standalone_query
            if query is None or not _preserves_terms(turn["question"], query):
                raise AnswerFailure("rewrite_changed_question")
    elif route == "fresh_retrieve":
        query = turn["question"]
    if route == "reuse_evidence":
        passages = reused
    elif route == "clarify":
        passages = []
    else:
        assert query is not None
        if embedder.count_tokens("query: " + query) > 512:
            raise AnswerFailure("query_token_limit_unusable")
        try:
            found = retrieve_evidence(db, org_id=org_id, as_of=as_of, query=query,
                                      category=None, corpus_version=turn["corpus_version"],
                                      embedder=embedder, top_k=8)
        except CorpusChanged as exc:
            raise AnswerFailure("corpus_changed", retryable=True) from exc
        passages = [{"passage_id": p.passage_id, "source_ref": p.source_ref,
                     "source_id": p.source_id, "version_id": p.version_id,
                     "source_version": p.source_version, "title": p.title,
                     "text": p.text, "effective_from": p.effective_from} for p in found]
    current_corpus = db.execute(text("""
        SELECT corpus_version FROM organisations WHERE id=:org_id
    """), {"org_id": org_id}).scalar_one()
    if current_corpus != turn["corpus_version"]:
        raise AnswerFailure("corpus_changed", retryable=True)
    if not passages and route not in {"clarify"}:
        route = "abstain"
    # The evidence snapshot and prompt metadata are durable before the answer call.
    db.execute(text("DELETE FROM chat_evidence WHERE org_id=:org_id AND turn_id=:turn_id"),
               {"org_id": org_id, "turn_id": turn_id})
    for rank, passage in enumerate(passages, 1):
        db.execute(text("""
            INSERT INTO chat_evidence(org_id,turn_id,passage_id,rank,supplied_text,source_version)
            VALUES (:org_id,:turn_id,:passage_id,:rank,:supplied_text,:source_version)
        """), {"org_id": org_id, "turn_id": turn_id, "passage_id": passage["passage_id"],
               "rank": rank, "supplied_text": passage["text"],
               "source_version": passage["source_version"]})
    db.execute(text("""
        UPDATE chat_turns SET state='processing',route=:route,rewritten_query=:query,
          reviewed_against_corpus_version=:corpus_version,evidence_as_of_date=:as_of,
          router_version=:router_version,rewrite_prompt_version=:rewrite_version,
          answer_prompt_version=:answer_version,model_id=NULL,provider_id=NULL,
          rewrite_provider_id=:rewrite_provider_id,rewrite_model_id=:rewrite_model_id,
          error_code=NULL
        WHERE org_id=:org_id AND id=:turn_id
    """), {"org_id": org_id, "turn_id": turn_id, "route": route, "query": query,
           "corpus_version": turn["corpus_version"], "as_of": as_of,
           "router_version": ROUTER_VERSION,
           "rewrite_version": REWRITE_VERSION if choice.route == "rewrite_retrieve" else None,
           "answer_version": ANSWER_VERSION if route not in {"clarify", "abstain"} else None,
           "rewrite_provider_id": rewrite_provider_id, "rewrite_model_id": rewrite_model_id})
    db.commit()
    return turn, passages, recent, route, clarification, query


def _publish(db: Session, *, org_id: UUID, turn_id: UUID, turn: dict[str, Any],
             passages: list[dict[str, Any]], route: str, answer_status: str,
             answer: str, citations: Sequence[Any], provider_id: str | None,
             model_id: str | None) -> None:
    current = db.execute(text("""
        SELECT t.state,r.revision_id,d.current_revision_id
        FROM chat_turns t JOIN reviews r ON r.org_id=t.org_id AND r.id=t.review_id
        JOIN draft_revisions v ON v.org_id=r.org_id AND v.id=r.revision_id
        JOIN drafts d ON d.org_id=v.org_id AND d.id=v.draft_id
        WHERE t.org_id=:org_id AND t.id=:turn_id FOR UPDATE OF t
    """), {"org_id": org_id, "turn_id": turn_id}).mappings().first()
    if (current is None or current["revision_id"] != turn["revision_id"]
            or current["current_revision_id"] != turn["revision_id"]):
        raise AnswerFailure("question_revision_changed")
    if current["state"] == "completed":
        return
    if current["state"] != "processing":
        raise AnswerFailure("question_not_processable")
    by_id = {p["passage_id"]: p for p in passages}
    for index, citation in enumerate(citations):
        passage = by_id[citation.passage_id]
        db.execute(text("""
            INSERT INTO chat_citations(org_id,turn_id,passage_id,source_ref,source_version,
                                       source_title,excerpt,effective_from,sort_order)
            VALUES (:org_id,:turn_id,:passage_id,:source_ref,:source_version,
                    :source_title,:excerpt,:effective_from,:sort_order)
        """), {"org_id": org_id, "turn_id": turn_id,
               "passage_id": citation.passage_id, "source_ref": passage["source_ref"],
               "source_version": passage["source_version"], "source_title": passage["title"],
               "excerpt": citation.supporting_quote,
               "effective_from": passage["effective_from"], "sort_order": index})
    db.execute(text("""
        UPDATE chat_turns SET state='completed',route=:route,answer_status=:answer_status,
          provider_id=:provider_id,model_id=:model_id,
          answer=:answer,validator_outcome='passed_exact_quote_checks',answered_at=now()
        WHERE org_id=:org_id AND id=:turn_id AND state='processing'
    """), {"org_id": org_id, "turn_id": turn_id, "route": route,
           "answer_status": answer_status, "answer": answer,
           "provider_id": provider_id, "model_id": model_id})
    db.commit()


def answer_question(db: Session, *, org_id: UUID, turn_id: UUID,
                    embedder: FollowupEmbedder, chat: AnswerChat,
                    today: date | None = None) -> None:
    """Process one queued turn; prior answers never become brand authority."""
    turn, passages, recent, route, clarification, query = _select(
        db, org_id=org_id, turn_id=turn_id, embedder=embedder, chat=chat, today=today,
    )
    if route == "completed":
        return
    provider_id: str | None = None
    model_id: str | None = None
    if route == "clarify":
        status, answer, citations = "clarification_needed", (
            clarification or "Which claim or finding do you mean?"), []
    elif route == "abstain":
        status, answer, citations = "insufficient_evidence", (
            "I could not find current approved guidance that answers this question. Please verify it with the brand administrator."), []
    else:
        finding = None
        if turn["finding_id"]:
            finding = db.execute(text("""
                SELECT quote,explanation FROM findings WHERE org_id=:org_id AND id=:finding_id
            """), {"org_id": org_id, "finding_id": turn["finding_id"]}).mappings().first()
        payload = {
            "question": turn["question"], "rewritten_query": query,
            "channel": turn["channel"], "draft_text": turn["content_nfc"][:4000],
            "finding": dict(finding) if finding else None, "recent_turns": recent,
            "current_evidence": [{"passage_id": str(p["passage_id"]),
                                  "source_ref": p["source_ref"], "source_version": p["source_version"],
                                  "title": p["title"], "text": p["text"]} for p in passages],
        }
        try:
            raw = chat.complete_json(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "developer", "content": ANSWER_PROMPT},
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
                schema_name="veriloom_grounded_answer_v2", schema=ANSWER_SCHEMA, max_tokens=4096,
            )
            validated = validate_answer(raw, current_passages={p["passage_id"]: p["text"] for p in passages})
            provider_id, model_id = completed_provenance(chat)
        except (SarvamError, OpenAICompatibleError) as exc:
            raise AnswerFailure("answer_provider_error", retryable=True) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise AnswerFailure("invalid_answer_output") from exc
        status, answer, citations = (validated.candidate.answer_status,
                                      validated.candidate.answer, validated.candidate.evidence)
    _publish(db, org_id=org_id, turn_id=turn_id, turn=turn, passages=passages,
             route=route, answer_status=status, answer=answer, citations=citations,
             provider_id=provider_id, model_id=model_id)


def build_answer_dependencies() -> tuple[FollowupEmbedder, SarvamChat | SarvamFirstChat]:
    from app.core.config import get_settings
    from app.rag.embeddings import MODEL_NAME, get_embedder

    settings = get_settings()
    if settings.EMBEDDING_MODEL != MODEL_NAME:
        raise RuntimeError("The configured embedding model does not match the pinned E5 adapter.")
    return get_embedder(), build_worker_chat(settings)
