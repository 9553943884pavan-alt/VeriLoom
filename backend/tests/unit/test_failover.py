"""Synthetic-only failover tests; no external provider calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.integrations.failover import SarvamFirstChat, SyntheticFailoverChat, completed_provenance
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


def test_worker_fallback_accepts_approved_material_and_clears_stale_provenance() -> None:
    primary = FakeChat("primary-model", SarvamTransientError("unavailable"))
    fallback = FakeChat("fallback-model", {"answer": "fallback"})
    chat = SarvamFirstChat(primary, fallback)
    assert chat.complete_json(MESSAGES, schema_name="s", schema=SCHEMA) == {"answer": "fallback"}
    assert completed_provenance(chat) == ("indierouter", "fallback-model")
    fallback.result = SarvamError("failed")
    with pytest.raises(SarvamError):
        chat.complete_json(MESSAGES, schema_name="s", schema=SCHEMA)
    assert (chat.last_provider, chat.last_model) == (None, None)


def test_worker_builder_uses_optional_configured_fallback(monkeypatch) -> None:
    from types import SimpleNamespace
    from pydantic import SecretStr
    import app.integrations.failover as module

    primary = FakeChat("primary-model", {"answer": "primary"})
    monkeypatch.setattr(module, "SarvamChat", lambda _settings: primary)
    settings = SimpleNamespace(INDIEROUTER_API_KEY=SecretStr(""),
                               INDIEROUTER_MODEL="fallback-model",
                               INDIEROUTER_CHAT_URL="https://example.com/v1/chat/completions")
    assert module.build_worker_chat(settings) is primary
    captured = {}
    fallback = FakeChat("fallback-model", {"answer": "fallback"})
    monkeypatch.setattr(module, "OpenAICompatibleChat", lambda **kwargs:
                        captured.update(kwargs) or fallback)
    settings.INDIEROUTER_API_KEY = SecretStr("test-only")
    chat = module.build_worker_chat(settings)
    assert isinstance(chat, SarvamFirstChat)
    assert captured == {"endpoint_url": settings.INDIEROUTER_CHAT_URL,
                        "model_id": "fallback-model", "api_key": "test-only",
                        "disable_thinking": True}
