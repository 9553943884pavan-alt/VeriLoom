"""Provider-neutral OpenAI-compatible chat completion adapter."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

CONNECT_TIMEOUT_SECONDS = 3.0
MAX_ATTEMPTS = 3
MAX_OUTPUT_TOKENS = 4096
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OpenAICompatibleError(RuntimeError):
    """A safe-to-report provider failure."""


class OpenAICompatibleConfigurationError(OpenAICompatibleError):
    """The endpoint or required provider configuration is invalid."""


class OpenAICompatibleTransientError(OpenAICompatibleError):
    """The provider remains temporarily unavailable after bounded retries."""


class OpenAICompatibleCapabilityError(OpenAICompatibleError):
    """The endpoint rejected strict JSON-schema response formatting."""


class OpenAICompatibleResponseError(OpenAICompatibleError):
    """The endpoint returned a response that cannot be safely consumed."""


class OpenAICompatibleChat:
    """Non-streaming strict JSON-schema client for OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        model_id: str,
        api_key: str,
        timeout_seconds: float = 90.0,
        max_attempts: int = MAX_ATTEMPTS,
        disable_thinking: bool = False,
        client: Any = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed_url = urlsplit(endpoint_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.fragment
        ):
            raise OpenAICompatibleConfigurationError(
                "endpoint_url must be an HTTPS URL without credentials or fragment."
            )
        if not model_id or not model_id.strip():
            raise OpenAICompatibleConfigurationError("model_id is required.")
        if not api_key and client is None:
            raise OpenAICompatibleConfigurationError("api_key is required.")
        if not 0 < timeout_seconds <= 120:
            raise OpenAICompatibleConfigurationError(
                "timeout_seconds must be greater than 0 and at most 120."
            )
        if not 1 <= max_attempts <= MAX_ATTEMPTS:
            raise OpenAICompatibleConfigurationError(
                f"max_attempts must be between 1 and {MAX_ATTEMPTS}."
            )

        self._endpoint_url = endpoint_url
        self._model_id = model_id
        self._api_key = api_key
        self._timeout = httpx.Timeout(
            timeout_seconds, connect=min(timeout_seconds, CONNECT_TIMEOUT_SECONDS)
        )
        self._max_attempts = max_attempts
        self._disable_thinking = disable_thinking
        self._sleeper = sleeper
        self._client = client or httpx.Client(timeout=self._timeout)
        self._owns_client = client is None

    @property
    def model(self) -> str:
        """Configured model identifier, suitable for metadata logs."""
        return self._model_id

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        if not messages:
            raise ValueError("messages must not be empty")
        if not isinstance(schema_name, str) or not schema_name.strip():
            raise ValueError("schema_name is required")
        if not isinstance(schema, Mapping):
            raise TypeError("schema must be a mapping")
        if not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}.")

        body = {
            "model": self._model_id,
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
        if self._disable_thinking:
            body["chat_template_kwargs"] = {"thinking": False}
        response = self._post_with_retries(body)
        try:
            payload = response.json()
            choice = payload["choices"][0]
            finish_reason = choice["finish_reason"]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise OpenAICompatibleResponseError("Provider returned an invalid response.") from None
        if finish_reason != "stop":
            raise OpenAICompatibleResponseError("Provider returned an incomplete response.")
        if not isinstance(content, str):
            raise OpenAICompatibleResponseError("Provider returned an invalid response.")
        try:
            decoded = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            raise OpenAICompatibleResponseError("Provider returned invalid JSON.") from None
        if not isinstance(decoded, dict):
            raise OpenAICompatibleResponseError("Provider returned invalid JSON.")
        return decoded

    def _post_with_retries(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(
                    self._endpoint_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.TransportError:
                if attempt == self._max_attempts:
                    raise OpenAICompatibleTransientError("Provider is temporarily unavailable.") from None
                self._sleeper(0.2 * (2 ** (attempt - 1)))
                continue

            if response.status_code == 400:
                raise OpenAICompatibleCapabilityError(
                    "Provider does not support the requested strict JSON-schema response format."
                )
            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt == self._max_attempts:
                    raise OpenAICompatibleTransientError("Provider is temporarily unavailable.")
                self._sleeper(0.2 * (2 ** (attempt - 1)))
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise OpenAICompatibleError("Provider could not complete the request.")
            return response
        raise OpenAICompatibleTransientError("Provider is temporarily unavailable.")

    def close(self) -> None:
        """Close the internally created HTTP client, if there is one."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenAICompatibleChat:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
