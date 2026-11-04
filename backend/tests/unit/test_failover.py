"""Synthetic-only failover tests; no external provider calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.integrations.failover import SyntheticFailoverChat
from app.integrations.sarvam import SarvamError, SarvamTransientError


class FakeChat:
    def __init__(self, model: str, result: dict[str, Any] | Exception) -> None:
        self.model = model
        self.result = result
        self.calls = 0
        self.closed = False

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self) -> None:
        self.closed = True


MESSAGES = [{"role": "user", "content": "Synthetic brand sentence"}]
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"answer": {"type": "string"}}}


def complete(chat: SyntheticFailoverChat, *, synthetic: bool = True) -> dict[str, Any]:
    return chat.complete_json(MESSAGES, schema_name="synthetic", schema=SCHEMA, synthetic=synthetic)


def test_primary_success_does_not_contact_fallback() -> None:
    primary = FakeChat("sarvam-105b", {"answer": "primary"})
    fallback = FakeChat("deepseek-v4.1-flash", {"answer": "fallback"})
    chat = SyntheticFailoverChat(primary, fallback)
    assert complete(chat) == {"answer": "primary"}
    assert (primary.calls, fallback.calls) == (1, 0)
    assert (chat.last_provider, chat.last_model) == ("sarvam", "sarvam-105b")


def test_exhausted_transient_error_falls_back_and_records_winner() -> None:
    primary = FakeChat("sarvam-105b", SarvamTransientError("unavailable"))
    fallback = FakeChat("deepseek-v4.1-flash", {"answer": "fallback"})
    chat = SyntheticFailoverChat(primary, fallback)
    assert complete(chat) == {"answer": "fallback"}
    assert (primary.calls, fallback.calls) == (1, 1)
    assert (chat.last_provider, chat.last_model) == ("indierouter", "deepseek-v4.1-flash")


def test_permanent_sarvam_error_does_not_fall_back() -> None:
    primary = FakeChat("sarvam-105b", SarvamError("bad request"))
    fallback = FakeChat("deepseek-v4.1-flash", {"answer": "fallback"})
    chat = SyntheticFailoverChat(primary, fallback)
    with pytest.raises(SarvamError):
        complete(chat)
    assert fallback.calls == 0


def test_private_call_is_refused_before_either_provider() -> None:
    primary = FakeChat("sarvam-105b", {"answer": "primary"})
    fallback = FakeChat("deepseek-v4.1-flash", {"answer": "fallback"})
    chat = SyntheticFailoverChat(primary, fallback)
    with pytest.raises(PermissionError):
        complete(chat, synthetic=False)
    assert (primary.calls, fallback.calls) == (0, 0)


def test_both_providers_close() -> None:
    primary = FakeChat("sarvam-105b", {"answer": "primary"})
    fallback = FakeChat("deepseek-v4.1-flash", {"answer": "fallback"})
    SyntheticFailoverChat(primary, fallback).close()
    assert primary.closed and fallback.closed
