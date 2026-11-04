"""Opt-in IndieRouter smoke with synthetic text only.

Run from ``backend`` after putting INDIEROUTER_API_KEY in the ignored root
``.env``. This probe does not load real drafts, guidance, or the database.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.core.config import get_settings
from app.integrations.failover import SyntheticFailoverChat
from app.integrations.openai_compatible import OpenAICompatibleChat, OpenAICompatibleError
from app.integrations.sarvam import SarvamTransientError
from app.jobs.handlers.answer_question import (
    ANSWER_PROMPT, ANSWER_SCHEMA, REWRITE_PROMPT, REWRITE_SCHEMA,
)
from app.jobs.handlers.review_revision import (
    DEVELOPER_PROMPT, INITIAL_REVIEW_SCHEMA, SYSTEM_PROMPT,
)
from app.rag.answer_validation import RewriteCandidate, validate_answer
from app.rag.review_validation import validate_review


class SyntheticUnavailablePrimary:
    model = "synthetic-unavailable-sarvam"

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        raise SarvamTransientError("Synthetic transient failure.")


def _check_product_schemas(chat: SyntheticFailoverChat) -> None:
    """Exercise the three real task schemas using invented, non-private data."""
    passage_id = UUID("00000000-0000-4000-8000-000000000001")
    passage_text = "Use the phrase Bright Plan. Do not claim guaranteed savings."
    draft = "Our plan guarantees savings."
    review = chat.complete_json(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "developer", "content": DEVELOPER_PROMPT},
         {"role": "user", "content": json.dumps({
             "task": "initial_review", "review_id": str(passage_id),
             "revision_id": str(passage_id), "review_as_of": "2026-11-05",
             "channel": "social", "draft_text": draft,
             "guidance": [{"passage_id": str(passage_id), "source_ref": "SYNTH-1",
                           "source_version": 1, "title": "Invented guidance",
                           "text": passage_text}],
         })}],
        schema_name="veriloom_initial_review_v2",
        schema=INITIAL_REVIEW_SCHEMA,
        max_tokens=4096,
        synthetic=True,
    )
    try:
        validate_review(review, revision_text=draft, supplied_passages={passage_id: passage_text})
    except (ValidationError, ValueError) as exc:
        raise SystemExit("Synthetic initial review failed local validation.") from exc
    print("Synthetic initial-review schema and local validation passed.")

    rewrite = chat.complete_json(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "developer", "content": REWRITE_PROMPT},
         {"role": "user", "content": json.dumps({
             "question": "Does the guidance prohibit guaranteed savings?",
             "finding": None, "recent_turns": [],
         })}],
        schema_name="veriloom_rewrite_followup_v2",
        schema=REWRITE_SCHEMA,
        max_tokens=4096,
        synthetic=True,
    )
    try:
        RewriteCandidate.model_validate(rewrite)
    except ValidationError as exc:
        raise SystemExit("Synthetic follow-up rewrite failed local validation.") from exc
    print("Synthetic follow-up rewrite schema and local validation passed.")

    answer = chat.complete_json(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "developer", "content": ANSWER_PROMPT},
         {"role": "user", "content": json.dumps({
             "question": "Does the guidance prohibit guaranteed savings?",
             "rewritten_query": "guidance guaranteed savings prohibition",
             "channel": "social", "draft_text": draft,
             "finding": None, "recent_turns": [],
             "current_evidence": [{"passage_id": str(passage_id),
                                   "source_ref": "SYNTH-1", "source_version": 1,
                                   "title": "Invented guidance", "text": passage_text}],
         })}],
        schema_name="veriloom_grounded_answer_v2",
        schema=ANSWER_SCHEMA,
        max_tokens=4096,
        synthetic=True,
    )
    try:
        validate_answer(answer, current_passages={passage_id: passage_text})
    except (ValidationError, ValueError) as exc:
        raise SystemExit("Synthetic grounded answer failed local validation.") from exc
    print("Synthetic grounded-answer schema and local validation passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", action="store_true", help="probe all three product schemas")
    args = parser.parse_args()
    settings = get_settings()
    key = settings.INDIEROUTER_API_KEY.get_secret_value()
    if not key:
        raise SystemExit("INDIEROUTER_API_KEY is missing from the ignored local .env.")
    fallback = OpenAICompatibleChat(
        endpoint_url=settings.INDIEROUTER_CHAT_URL,
        model_id=settings.INDIEROUTER_MODEL,
        api_key=key,
        max_attempts=1,
        disable_thinking=True,
    )
    chat = SyntheticFailoverChat(SyntheticUnavailablePrimary(), fallback)
    try:
        result = chat.complete_json(
            [{"role": "user", "content": "Synthetic test: reply with a short greeting."}],
            schema_name="synthetic_greeting",
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["greeting"],
                "properties": {"greeting": {"type": "string"}},
            },
            max_tokens=100,
            synthetic=True,
        )
        if args.schemas:
            try:
                _check_product_schemas(chat)
            except OpenAICompatibleError as exc:
                raise SystemExit(f"Synthetic product-schema probe failed: {exc}") from None
    finally:
        chat.close()
    if set(result) != {"greeting"} or not isinstance(result["greeting"], str):
        raise SystemExit("IndieRouter returned JSON outside the synthetic test schema.")
    print(f"Synthetic failover succeeded: provider={chat.last_provider}, model={chat.last_model}")


if __name__ == "__main__":
    main()
