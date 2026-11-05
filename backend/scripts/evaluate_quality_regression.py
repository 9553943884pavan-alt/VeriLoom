"""Synthetic development checks for review/follow-up quality, never holdout labels.

Run from backend: python -m scripts.evaluate_quality_regression --live --repeats 2
No database/storage changes. Only invented inputs go to the real Sarvam adapter.
Results contain measurements and pass/fail checks, never raw model messages.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from app.integrations.failover import completed_provenance
from app.integrations.sarvam import SarvamChat
from app.jobs.handlers.answer_question import (
    ANSWER_PROMPT, ANSWER_SCHEMA, ANSWER_VERSION, REWRITE_PROMPT, REWRITE_SCHEMA,
    REWRITE_VERSION, _preserves_terms,
)
from app.jobs.handlers.review_revision import (
    DEVELOPER_PROMPT, INITIAL_REVIEW_SCHEMA, PROMPT_VERSION, SYSTEM_PROMPT,
)
from app.rag.answer_validation import RewriteCandidate, validate_answer
from app.rag.retrieval.followup import route_followup
from app.rag.review_validation import validate_review

ROOT = Path(__file__).resolve().parents[2]
PASSAGE_ID = UUID("00000000-0000-4000-8000-000000000071")
COFFEE = (
    "Do not describe our coffee as the best.\nUse a clear, friendly tone.\n"
    "Approved factual claim: Our coffee is freshly roasted.\n"
    "If a claim is not listed as approved, request human review before publication."
)
BOTTLE = "Approved factual claim: The Aurora bottle holds 500 ml.\nDo not claim it is unbreakable."
APPROVED = "Approved factual claim: Our coffee is freshly roasted."


@dataclass(frozen=True)
class Case:
    case_id: str
    task: str
    draft: str
    guidance: str
    question: str = ""
    expected_findings: int = 0
    evidence_status: str = "supported"
    issue: str = ""
    answer_status: str = "answered"
    supporting_quote: str = ""
    recent_turns: tuple[tuple[str, str], ...] = ()


# These labels are only consulted AFTER generation. They never enter messages.
CASES = (
    Case("approved_coffee", "review", "Our coffee is freshly roasted.", COFFEE),
    Case("approved_capacity", "review", "The Aurora bottle holds 500 ml.", BOTTLE),
    Case("real_violation", "review", "Our coffee is the best.", COFFEE,
         expected_findings=1, issue="the best"),
    Case("mixed_claims", "review", "Our coffee is freshly roasted. Our coffee is the best.", COFFEE,
         expected_findings=1, issue="the best"),
    Case("unknown_fact", "review", "Our coffee contains 12 mg of caffeine.", COFFEE,
         expected_findings=1, evidence_status="insufficient", issue="12 mg"),
    Case("opinion", "review", "I love Naruto.", "Use a clear, friendly tone."),
    Case("approved_wording", "answer", "Our coffee is the best.", COFFEE,
         question="The suggested edit says 'Our coffee is freshly roasted.' "
                  "Is that exact sentence approved by the guidance?", supporting_quote=APPROVED),
    Case("approved_other_fact", "answer", "The Aurora bottle holds 500 ml.", BOTTLE,
         question="Is 'The Aurora bottle holds 500 ml.' an approved fact?",
         supporting_quote="Approved factual claim: The Aurora bottle holds 500 ml."),
    Case("explicit_prohibition", "answer", "Our coffee is the best.", COFFEE,
         question="Does the guidance prohibit describing our coffee as 'the best'?",
         supporting_quote="Do not describe our coffee as the best."),
    Case("unknown_warranty", "answer", "Our coffee is freshly roasted.", COFFEE,
         question="Does our coffee have a lifetime warranty?", answer_status="insufficient_evidence",
         recent_turns=(("Does it have a warranty?", "Yes, lifetime warranty."),)),
    Case("conflicting_approval", "answer", "Our coffee is freshly roasted.",
         COFFEE + "\nDo not claim that our coffee is freshly roasted.",
         question="Is 'Our coffee is freshly roasted.' permitted by all the current guidance?",
         answer_status="insufficient_evidence"),
    Case("contextual_approval", "answer", "Our coffee is the best.", COFFEE,
         question="Is that exact replacement approved in the guidance?", supporting_quote=APPROVED,
         recent_turns=(("What replacement can I use?", "Proposed replacement: Our coffee is freshly roasted."),)),
)


def messages_for(case: Case) -> list[dict[str, str]]:
    passage = {"passage_id": str(PASSAGE_ID), "source_ref": "SYNTH-QUALITY#1",
               "source_version": "1.0", "title": "Synthetic guidance", "text": case.guidance}
    payload: dict[str, Any] = {"draft_text": case.draft, "channel": "social_post"}
    if case.task == "review":
        payload.update(task="initial_review", guidance=[passage])
    else:
        payload.update(question=case.question, finding=None, current_evidence=[passage],
                       review_coverage={"status": "sufficient", "reason": None},
                       recent_turns=[{"question": q, "answer": a} for q, a in case.recent_turns])
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "developer", "content": DEVELOPER_PROMPT if case.task == "review" else ANSWER_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def assess(case: Case, raw: object, *, passages: dict[UUID, str] | None = None) -> dict[str, Any]:
    """Use the production default validator before any development judgments."""
    supplied = passages if passages is not None else {PASSAGE_ID: case.guidance}
    if case.task == "review":
        result = validate_review(raw, revision_text=case.draft,
                                 supplied_passages=supplied)
        checks = {
            "finding_count": len(result.findings) == case.expected_findings,
            "expected_issue": all(case.issue in (f.candidate.quote or "")
                                  and f.candidate.evidence_status == case.evidence_status
                                  for f in result.findings),
            "anchors": all(f.anchor_start is not None and f.anchor_end is not None
                           and case.draft[f.anchor_start:f.anchor_end] == f.candidate.quote
                           for f in result.findings),
            "no_identical_edit": all(f.candidate.suggested_edit != f.candidate.quote
                                     or f.candidate.suggested_edit is None for f in result.findings),
            "truthful_missing_coverage": case.evidence_status != "insufficient"
                                         or result.coverage != "sufficient",
        }
        return {"passed": all(checks.values()), "checks": checks,
                "findings": len(result.findings), "coverage": result.coverage}
    answer = validate_answer(raw, current_passages=supplied).candidate
    checks = {"answer_status": answer.answer_status == case.answer_status,
              "required_quote": not case.supporting_quote or any(
                  case.supporting_quote in e.supporting_quote for e in answer.evidence)}
    return {"passed": all(checks.values()), "checks": checks, "answer_status": answer.answer_status}


def run(chat: Any, *, repeats: int = 1, cases: tuple[Case, ...] = CASES,
        retrieve: Callable[[str], list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    if not 1 <= repeats <= 3:
        raise ValueError("repeats must be between 1 and 3")
    rows = []
    logical_calls = 0
    for repeat in range(1, repeats + 1):
        for case in cases:
            row: dict[str, Any] = {"case_id": case.case_id, "repeat": repeat, "task": case.task}
            started = time.perf_counter()
            try:
                messages = messages_for(case)
                if case.task == "answer":
                    route = route_followup(case.question, has_finding=False,
                                           current_finding_evidence=False,
                                           has_recent_context=bool(case.recent_turns)).route
                    row["route"] = route
                    if route == "rewrite_retrieve":
                        payload = json.loads(messages[-1]["content"])
                        context = {k: payload[k] for k in
                                   ("question", "finding", "review_coverage", "recent_turns")}
                        logical_calls += 1
                        rewritten = RewriteCandidate.model_validate(chat.complete_json(
                            [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "developer", "content": REWRITE_PROMPT},
                             {"role": "user", "content": json.dumps(context)}],
                            schema_name="veriloom_rewrite_followup_v2", schema=REWRITE_SCHEMA,
                            max_tokens=4096,
                        ))
                        if rewritten.needs_clarification or not _preserves_terms(
                            case.question, rewritten.standalone_query or "",
                        ):
                            raise ValueError("rewrite did not resolve the synthetic question")
                        payload["rewritten_query"] = rewritten.standalone_query
                        messages[-1]["content"] = json.dumps(payload)
                supplied = None
                if retrieve is not None:
                    payload = json.loads(messages[-1]["content"])
                    query = (payload.get("rewritten_query") or case.question
                             if case.task == "answer" else case.draft)
                    retrieval_start = time.perf_counter()
                    evidence = retrieve(query)
                    row["retrieval_ms"] = round((time.perf_counter() - retrieval_start) * 1000, 3)
                    row["retrieved_passages"] = len(evidence)
                    supplied = {UUID(p["passage_id"]): p["text"] for p in evidence}
                    payload["guidance" if case.task == "review" else "current_evidence"] = evidence
                    messages[-1]["content"] = json.dumps(payload)
                logical_calls += 1
                raw = chat.complete_json(
                    messages,
                    schema_name="veriloom_initial_review_v2" if case.task == "review"
                    else "veriloom_grounded_answer_v2",
                    schema=INITIAL_REVIEW_SCHEMA if case.task == "review" else ANSWER_SCHEMA,
                    max_tokens=4096,
                )
                row["provider"], row["model"] = completed_provenance(chat)
                row.update(assess(case, raw, passages=supplied))
            except Exception as exc:
                row.update(passed=False, error_type=type(exc).__name__)
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            rows.append(row)
    return {"scope": "synthetic development smoke; not independent holdout or accuracy estimate",
            "initial_review_version": PROMPT_VERSION, "answer_version": ANSWER_VERSION,
            "rewrite_version": REWRITE_VERSION,
            "retrieval": "read-only PostgreSQL / E5 / BM25 / RRF" if retrieve is not None
            else "supplied synthetic evidence; no database/retrieval exercised",
            "validator": "production default; Stage 3 filter disabled",
            "logical_calls": logical_calls, "case_runs": len(rows),
            "passed": sum(row["passed"] for row in rows),
            "all_passed": all(row["passed"] for row in rows), "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/ai_quality_regression_result.json")
    parser.add_argument("--demo-org-id", type=UUID, help="read-only retrieval in an approved synthetic organisation")
    parser.add_argument("--synthetic-guidance-file", type=Path,
                        help="require every retrieved passage to occur verbatim in this known synthetic file")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for bounded synthetic Sarvam calls")
    if args.demo_org_id and not args.synthetic_guidance_file:
        parser.error("--demo-org-id requires --synthetic-guidance-file")
    chat = SarvamChat(max_attempts=1, timeout_seconds=30)
    try:
        if args.demo_org_id:
            from sqlalchemy import text
            from sqlalchemy.orm import Session

            from app.db.session import get_engine
            from app.rag.embeddings import get_embedder
            from app.rag.retrieval.hybrid import retrieve_evidence

            known = args.synthetic_guidance_file.read_text(encoding="utf-8")
            embedder = get_embedder()
            with Session(bind=get_engine()) as db:
                db.execute(text("SET TRANSACTION READ ONLY"))
                corpus = db.execute(text("SELECT corpus_version FROM organisations WHERE id=:org"),
                                    {"org": args.demo_org_id}).scalar_one()

                def retrieve(query: str) -> list[dict[str, Any]]:
                    found = retrieve_evidence(db, org_id=args.demo_org_id, as_of=date.today(),
                                              query=query, category=None, corpus_version=corpus,
                                              embedder=embedder, top_k=8)
                    if not found or any(p.text.strip() not in known for p in found):
                        raise ValueError("retrieval did not return exclusively known synthetic guidance")
                    return [{"passage_id": str(p.passage_id), "source_ref": p.source_ref,
                             "source_version": p.source_version, "title": p.title, "text": p.text}
                            for p in found]

                selected = tuple(c for c in CASES if c.case_id in {
                    "approved_coffee", "real_violation", "approved_wording", "unknown_warranty",
                })
                result = run(chat, repeats=args.repeats, cases=selected, retrieve=retrieve)
                db.rollback()
        else:
            result = run(chat, repeats=args.repeats)
    finally:
        chat.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
