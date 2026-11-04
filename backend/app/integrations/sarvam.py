"""Server-side Sarvam V2 chat completion adapter."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping, Sequence

import httpx

from app.core.config import Settings, get_settings

CHAT_COMPLETIONS_URL = "https://api.sarvam.ai/v2/chat/completions"
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 90.0
MAX_ATTEMPTS = 3
MAX_OUTPUT_TOKENS = 4096
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class SarvamConfigurationError(RuntimeError):
    """Sarvam credentials or model configuration is incomplete."""


class SarvamError(RuntimeError):
    """A safe-to-report provider failure."""


class SarvamResponseError(SarvamError):
    """The provider returned a response that cannot be safely consumed."""


class SarvamChat:
    """Non-streaming V2 JSON-schema chat client with bounded transient retries.

    ``client`` and ``sleeper`` can be injected for offline tests. The adapter
    returns decoded JSON only; callers remain responsible for task-specific
    schema and grounding validation.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any = None,
        timeout_seconds: float = READ_TIMEOUT_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = self._settings.SARVAM_API_KEY.get_secret_value()
        self._model = self._settings.SARVAM_MODEL
        if not self._model:
            raise SarvamConfigurationError("Sarvam model is not configured.")
        if not self._api_key and client is None:
            raise SarvamConfigurationError("Sarvam API is not configured.")
        if not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be greater than 0 and at most 120.")
        if not 1 <= max_attempts <= MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}.")
        self._timeout = httpx.Timeout(
            timeout_seconds, connect=min(timeout_seconds, CONNECT_TIMEOUT_SECONDS)
        )
        self._max_attempts = max_attempts
        self._sleeper = sleeper
        self._client = client or httpx.Client(timeout=self._timeout)
        self._owns_client = client is None

    @property
    def model(self) -> str:
        """Configured server-side model identifier, suitable for metadata logs."""
        return self._model

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Request strict structured JSON from Sarvam V2 and decode its content."""
        if not messages:
            raise ValueError("messages must not be empty")
        if not isinstance(schema_name, str) or not schema_name.strip():
            raise ValueError("schema_name is required")
        if not isinstance(schema, Mapping):
            raise TypeError("schema must be a mapping")
        if not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}.")

        body = {
            "model": self._model,
            "messages": [dict(message) for message in messages],
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                },
            },
        }
        # Sarvam-105B otherwise spends the completion budget on hidden reasoning;
        # JSON extraction needs its budget for the schema-constrained response.
        if self._model == "sarvam-105b":
            body["reasoning_effort"] = "none"
        response = self._post_with_retries(body)
        try:
            payload = response.json()
            choice = payload["choices"][0]
            finish_reason = choice["finish_reason"]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise SarvamResponseError("Sarvam returned an invalid response.") from None
        if finish_reason != "stop":
            raise SarvamResponseError("Sarvam returned an incomplete response.")
        if not isinstance(content, str):
            raise SarvamResponseError("Sarvam returned an invalid response.")
        try:
            decoded = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            raise SarvamResponseError("Sarvam returned invalid JSON.") from None
        if not isinstance(decoded, dict):
            raise SarvamResponseError("Sarvam returned invalid JSON.")
        return decoded

    def _post_with_retries(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(
                    CHAT_COMPLETIONS_URL,
                    headers={"api-subscription-key": self._api_key},
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.TransportError:
                if attempt == self._max_attempts:
                    raise SarvamError("Sarvam is temporarily unavailable.") from None
                self._sleeper(0.2 * (2 ** (attempt - 1)))
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt == self._max_attempts:
                    raise SarvamError("Sarvam is temporarily unavailable.")
                self._sleeper(0.2 * (2 ** (attempt - 1)))
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise SarvamError("Sarvam could not complete the request.")
            return response
        raise SarvamError("Sarvam is temporarily unavailable.")

    def close(self) -> None:
        """Close the internally created HTTP client, if there is one."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SarvamChat:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
