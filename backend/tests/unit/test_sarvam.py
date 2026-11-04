"""Offline tests for the Sarvam V2 adapter."""

from types import SimpleNamespace
import unittest

import httpx
from pydantic import SecretStr

from app.integrations.sarvam import (
    CHAT_COMPLETIONS_URL,
    MAX_ATTEMPTS,
    SarvamChat,
    SarvamConfigurationError,
    SarvamError,
    SarvamResponseError,
)


def configured_settings(key: str = "test-secret") -> SimpleNamespace:
    return SimpleNamespace(SARVAM_API_KEY=SecretStr(key), SARVAM_MODEL="test-model")


class FakeResponse:
    def __init__(self, status_code=200, content='{"answer":"ok"}', finish="stop"):
        self.status_code = status_code
        self.body = {
            "choices": [
                {"finish_reason": finish, "message": {"content": content}}
            ]
        }

    def json(self):
        return self.body


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


class SarvamChatTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient([FakeResponse()])
        self.delays = []
        self.chat = SarvamChat(
            configured_settings(), client=self.client, sleeper=self.delays.append
        )
        self.messages = [{"role": "user", "content": "Question"}]
        self.schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

    def complete(self):
        return self.chat.complete_json(
            self.messages, schema_name="test_schema", schema=self.schema
        )

    def test_sends_v2_strict_json_request_and_returns_decoded_object(self):
        self.assertEqual(self.complete(), {"answer": "ok"})
        url, request = self.client.calls[0]
        self.assertEqual(url, CHAT_COMPLETIONS_URL)
        self.assertEqual(request["headers"]["api-subscription-key"], "test-secret")
        self.assertEqual(request["json"]["model"], "test-model")
        self.assertFalse(request["json"]["stream"])
        self.assertEqual(request["json"]["max_tokens"], 2048)
        self.assertEqual(
            request["json"]["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "test_schema",
                    "strict": True,
                    "schema": self.schema,
                },
            },
        )

    def test_retries_retryable_status_with_bounded_exponential_backoff(self):
        client = FakeClient([FakeResponse(429), FakeResponse(503), FakeResponse()])
        chat = SarvamChat(configured_settings(), client=client, sleeper=self.delays.append)
        self.assertEqual(chat.complete_json(self.messages, schema_name="s", schema=self.schema), {"answer": "ok"})
        self.assertEqual(len(client.calls), MAX_ATTEMPTS)
        self.assertEqual(self.delays, [0.2, 0.4])

    def test_sarvam_105b_disables_reasoning_for_structured_json(self):
        settings = configured_settings()
        settings.SARVAM_MODEL = "sarvam-105b"
        client = FakeClient([FakeResponse()])
        chat = SarvamChat(settings, client=client)
        self.assertEqual(chat.complete_json(self.messages, schema_name="s", schema=self.schema), {"answer": "ok"})
        self.assertEqual(client.calls[0][1]["json"]["reasoning_effort"], "none")

    def test_retries_transport_failure_then_succeeds(self):
        client = FakeClient([httpx.ConnectError("sensitive"), FakeResponse()])
        chat = SarvamChat(configured_settings(), client=client, sleeper=self.delays.append)
        self.assertEqual(chat.complete_json(self.messages, schema_name="s", schema=self.schema), {"answer": "ok"})
        self.assertEqual(self.delays, [0.2])

    def test_provider_failures_are_safe_and_stop_after_three_attempts(self):
        client = FakeClient([FakeResponse(500), FakeResponse(503), FakeResponse(429)])
        chat = SarvamChat(configured_settings(), client=client, sleeper=self.delays.append)
        with self.assertRaises(SarvamError) as caught:
            chat.complete_json(self.messages, schema_name="s", schema=self.schema)
        self.assertEqual(str(caught.exception), "Sarvam is temporarily unavailable.")
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(len(client.calls), MAX_ATTEMPTS)
        self.assertEqual(self.delays, [0.2, 0.4])

    def test_nonretryable_http_error_is_sanitized_without_retry(self):
        client = FakeClient([FakeResponse(401)])
        chat = SarvamChat(configured_settings(), client=client, sleeper=self.delays.append)
        with self.assertRaisesRegex(SarvamError, "could not complete"):
            chat.complete_json(self.messages, schema_name="s", schema=self.schema)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.delays, [])

    def test_invalid_json_and_truncated_output_are_rejected(self):
        for response in (FakeResponse(content="not json"), FakeResponse(finish="length")):
            chat = SarvamChat(configured_settings(), client=FakeClient([response]))
            with self.assertRaises(SarvamResponseError):
                chat.complete_json(self.messages, schema_name="s", schema=self.schema)

    def test_malformed_provider_envelope_is_rejected(self):
        response = FakeResponse()
        response.body = {"choices": []}
        chat = SarvamChat(configured_settings(), client=FakeClient([response]))
        with self.assertRaises(SarvamResponseError):
            chat.complete_json(self.messages, schema_name="s", schema=self.schema)

    def test_missing_key_requires_configuration_unless_client_is_injected(self):
        with self.assertRaises(SarvamConfigurationError):
            SarvamChat(configured_settings(""))
        # An injected client supports offline use without a real credential.
        SarvamChat(configured_settings(""), client=FakeClient([FakeResponse()]))

    def test_request_limits_are_enforced_before_provider_call(self):
        for kwargs in (
            {"schema_name": "s", "max_tokens": 0},
            {"schema_name": "s", "max_tokens": 4097},
            {"schema_name": "", "max_tokens": 2048},
        ):
            with self.assertRaises(ValueError):
                self.chat.complete_json(self.messages, schema=self.schema, **kwargs)
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
