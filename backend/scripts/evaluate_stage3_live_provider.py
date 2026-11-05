"""Bounded synthetic Stage 3 generation probe; no database or fixture labels.

Run from backend with ``python -m scripts.evaluate_stage3_live_provider --live``.
Only aggregate scores and sanitized failure metadata are written to the result.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.core.config import get_settings
from app.integrations.failover import SarvamFirstChat, completed_provenance
from app.integrations.openai_compatible import OpenAICompatibleChat
from app.integrations.sarvam import SarvamChat
from app.jobs.handlers.review_revision import (
    DEVELOPER_PROMPT, INITIAL_REVIEW_SCHEMA, SYSTEM_PROMPT,
)
from app.rag.review_validation import ValidatedReview, validate_review

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "data/evaluation/stage3_live_provider_result.json"
PASSAGE_ID = UUID("00000000-0000-4000-8000-000000000071")


@dataclass(frozen=True)
class ProbeCase:
    case_id: str
    draft: str
    guidance: str
    expected: tuple[tuple[str, str], ...]


# Invented names and rules. These are short smoke cases, never Stage 3 holdout.
CASES = (
    ProbeCase("direct_violation", "Bright Plan guarantees savings.",
              "Do not claim guaranteed savings for Bright Plan.",
              (("promotional_claim", "guarantees savings"),)),
    ProbeCase("compliant_term", "Try Bright Plan today.",
              "Preferred term: Bright Plan.", ()),
    ProbeCase("hindi_claim", "Bright Plan से बचत की गारंटी है।",
              "Bright Plan के लिए बचत की गारंटी का दावा न करें।",
              (("promotional_claim", "बचत की गारंटी"),)),
)


def messages_for(case: ProbeCase) -> list[dict[str, str]]:
    user = {
        "task": "initial_review", "review_id": str(PASSAGE_ID),
        "revision_id": str(PASSAGE_ID), "review_as_of": "2026-11-05",
        "channel": "social", "draft_text": case.draft,
        "guidance": [{"passage_id": str(PASSAGE_ID), "source_ref": "SYNTH-71",
                      "source_version": 1, "title": "Invented guidance",
                      "text": case.guidance}],
    }
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "developer", "content": DEVELOPER_PROMPT},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def safe_error(exc: Exception) -> dict[str, Any]:
    """Never include provider body, draft text, quotes, or Pydantic input."""
    result: dict[str, Any] = {"type": type(exc).__name__}
    if isinstance(exc, ValidationError):
        result["fields"] = [".".join(map(str, item["loc"]))
                            for item in exc.errors(include_input=False, include_url=False)]
    return result


def assess(case: ProbeCase, payload: dict[str, Any], *, quality_filter: bool) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        review: ValidatedReview = validate_review(
            payload, revision_text=case.draft,
            supplied_passages={PASSAGE_ID: case.guidance},
            quality_filter=quality_filter,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return {"accepted": False, "error": safe_error(exc),
                "validator_ms": round((time.perf_counter() - start) * 1000, 3)}
    expected = set(case.expected)
    predicted = {(item.candidate.category, item.candidate.quote) for item in review.findings}
    # Exact quote matching is deliberately strict; paraphrased spans are scored
    # as missed here even if they refer to the same synthetic issue.
    anchors_valid = sum(item.anchor_start is not None and item.anchor_end is not None
                        and case.draft[item.anchor_start:item.anchor_end] == item.candidate.quote
                        for item in review.findings)
    cited = sum(bool(item.candidate.evidence) for item in review.findings)
    correctly_cited = sum(
        (item.candidate.category, item.candidate.quote) in expected
        and all(claim.passage_id == PASSAGE_ID
                and claim.supporting_quote in case.guidance
                for claim in item.candidate.evidence)
        for item in review.findings if item.candidate.evidence
    )
    return {
        "accepted": True, "coverage": review.coverage,
        "findings": len(review.findings), "true_positive": len(predicted & expected),
        "false_positive": len(predicted - expected), "false_negative": len(expected - predicted),
        "cited_findings": cited, "expected_issue_citations": correctly_cited,
        "exact_anchors": anchors_valid,
        "no_op_edits": sum(item.candidate.suggested_edit == item.candidate.quote
                           and item.candidate.suggested_edit is not None
                           for item in review.findings),
        "validator_ms": round((time.perf_counter() - start) * 1000, 3),
    }


def run(chat: Any, cases: tuple[ProbeCase, ...] = CASES) -> dict[str, Any]:
    results = []
    for case in cases[:3]:
        start = time.perf_counter()
        row: dict[str, Any] = {"case_id": case.case_id}
        try:
            payload = chat.complete_json(
                messages_for(case), schema_name="veriloom_initial_review_v2",
                schema=INITIAL_REVIEW_SCHEMA, max_tokens=4096,
            )
            row["generation_ms"] = round((time.perf_counter() - start) * 1000, 3)
            row["provider"], row["model"] = completed_provenance(chat)
            row["baseline"] = assess(case, payload, quality_filter=False)
            row["stage3"] = assess(case, payload, quality_filter=True)
        except Exception as exc:
            row["generation_ms"] = round((time.perf_counter() - start) * 1000, 3)
            row["generation_error"] = safe_error(exc)
        results.append(row)
    return {"dataset": "three fixed synthetic smoke cases; not independent holdout",
            "max_logical_completions": 3, "cases": results}


def live_chat() -> SarvamChat | SarvamFirstChat:
    settings = get_settings()
    primary = SarvamChat(settings, timeout_seconds=30, max_attempts=1)
    key = settings.INDIEROUTER_API_KEY.get_secret_value()
    if not key:
        return primary
    try:
        fallback = OpenAICompatibleChat(
            endpoint_url=settings.INDIEROUTER_CHAT_URL,
            model_id=settings.INDIEROUTER_MODEL, api_key=key,
            timeout_seconds=30, max_attempts=1, disable_thinking=True,
        )
    except Exception:
        primary.close()
        raise
    return SarvamFirstChat(primary, fallback)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="authorize at most three synthetic calls")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required to make provider calls")
    chat = live_chat()
    try:
        result = run(chat)
    finally:
        chat.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    RESULT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
