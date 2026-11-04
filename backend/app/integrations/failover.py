"""Opt-in synthetic-only Sarvam to IndieRouter failover probe.

This module is deliberately not wired into the production worker. The user
approved synthetic testing of the fallback, not transfer of private drafts or
guidance to IndieRouter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.integrations.sarvam import SarvamTransientError


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


class SyntheticFailoverChat:
    """Use IndieRouter only after Sarvam has exhausted transient retries.

    Callers must explicitly mark each request as synthetic. There is no worker
    integration, so real organisation material remains on Sarvam.
    """

    def __init__(self, primary: StructuredChat, fallback: StructuredChat) -> None:
        self._primary = primary
        self._fallback = fallback
        self.last_provider: str | None = None
        self.last_model: str | None = None

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
            raise PermissionError("IndieRouter failover is limited to synthetic tests.")
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
