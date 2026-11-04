"""Sarvam-first structured chat with IndieRouter on exhausted transient errors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.core.config import Settings
from app.integrations.openai_compatible import OpenAICompatibleChat
from app.integrations.sarvam import SarvamChat, SarvamTransientError


class StructuredChat(Protocol):
    @property
    def model(self) -> str: ...

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        max_tokens: int = 2048,
    ) -> dict[str, Any]: ...


class SarvamFirstChat:
    """Use IndieRouter only after Sarvam has exhausted transient retries."""

    def __init__(self, primary: StructuredChat, fallback: StructuredChat) -> None:
        self._primary = primary
        self._fallback = fallback
        self.last_provider: str | None = None
        self.last_model: str | None = None

    @property
    def model(self) -> str:
        """The primary model; the winning model is available after completion."""
        return self._primary.model

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        self.last_provider = None
        self.last_model = None
        try:
            result = self._primary.complete_json(
                messages, schema_name=schema_name, schema=schema, max_tokens=max_tokens
            )
        except SarvamTransientError:
            result = self._fallback.complete_json(
                messages, schema_name=schema_name, schema=schema, max_tokens=max_tokens
            )
            self.last_provider = "indierouter"
            self.last_model = self._fallback.model
            return result
        self.last_provider = "sarvam"
        self.last_model = self._primary.model
        return result

    def close(self) -> None:
        """Close owned provider resources when they expose ``close``."""
        for provider in (self._primary, self._fallback):
            close = getattr(provider, "close", None)
            if close is not None:
                close()


class SyntheticFailoverChat(SarvamFirstChat):
    """Keep the standalone probe limited to invented data."""

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        max_tokens: int = 2048,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        if not synthetic:
            raise PermissionError("This standalone probe accepts synthetic inputs only.")
        return super().complete_json(
            messages, schema_name=schema_name, schema=schema, max_tokens=max_tokens
        )


def build_worker_chat(settings: Settings) -> SarvamChat | SarvamFirstChat:
    """Use the approved fallback only when its credential is configured."""
    primary = SarvamChat(settings)
    key = settings.INDIEROUTER_API_KEY.get_secret_value()
    if not key:
        return primary
    try:
        fallback = OpenAICompatibleChat(
            endpoint_url=settings.INDIEROUTER_CHAT_URL,
            model_id=settings.INDIEROUTER_MODEL,
            api_key=key,
            disable_thinking=True,
        )
    except Exception:
        primary.close()
        raise
    return SarvamFirstChat(primary, fallback)


def completed_provenance(chat: StructuredChat) -> tuple[str, str]:
    """Return the provider/model for a successful completion, including test doubles."""
    provider = getattr(chat, "last_provider", None) or "sarvam"
    model = getattr(chat, "last_model", None) or chat.model
    return provider, model
