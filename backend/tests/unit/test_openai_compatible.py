"""Offline tests for the OpenAI-compatible chat adapter."""

from __future__ import annotations

import unittest

import httpx

from app.integrations.openai_compatible import (
    MAX_ATTEMPTS,
    OpenAICompatibleCapabilityError,
    OpenAICompatibleChat,
    OpenAICompatibleConfigurationError,
    OpenAICompatibleError,
    OpenAICompatibleResponseError,
)


class FakeResponse:
    def __init__(self, status_code=200, content='{"answer":"ok"}', finish="stop"):
        self.status_code = status_code
        self.body = {"choices": [{"finish_reason": finish, "message": {"content": content}}]}

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


class OpenAICompatibleChatTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = "https://gateway.example/v1/chat/completions"
        self.client = FakeClient([FakeResponse()])
        self.delays = []
        self.chat = OpenAICompatibleChat(
            endpoint_url=self.endpoint,
            model_id="configured-model",
            api_key="test-secret",
            client=self.client,
            sleeper=self.delays.append,
        )
        self.messages = [{"role": "user", "content": "Question"}]
        self.schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

    def complete(self):
        return self.chat.complete_json(self.messages, schema_name="answer", schema=self.schema)

    def test_sends_configured_endpoint_bearer_and_strict_schema_request(self):
        self.assertEqual(self.complete(), {"answer": "ok"})
        url, request = self.client.calls[0]
        self.assertEqual(url, self.endpoint)
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-secret")
        self.assertEqual(request["json"]["model"], "configured-model")
        self.assertEqual(request["json"]["response_format"], {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": self.schema},
        })

    def test_rejects_non_https_or_credentialed_endpoint(self):
        for endpoint in (
            "http://gateway.example/v1/chat/completions",
            "https://user:password@gateway.example/v1/chat/completions",
            "//gateway.example/v1/chat/completions",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(OpenAICompatibleConfigurationError):
                OpenAICompatibleChat(endpoint_url=endpoint, model_id="m", api_key="k")

    def test_optional_thinking_control_is_sent_only_when_enabled(self):
        self.complete()
        self.assertNotIn("chat_template_kwargs", self.client.calls[0][1]["json"])
        client = FakeClient([FakeResponse()])
        chat = OpenAICompatibleChat(
            endpoint_url=self.endpoint, model_id="m", api_key="k",
            disable_thinking=True, client=client,
        )
        chat.complete_json(self.messages, schema_name="answer", schema=self.schema)
        self.assertEqual(client.calls[0][1]["json"]["chat_template_kwargs"], {"thinking": False})

    def test_400_is_a_safe_capability_error_without_retry_or_downgrade(self):
        client = FakeClient([FakeResponse(400)])
        chat = OpenAICompatibleChat(
            endpoint_url=self.endpoint, model_id="m", api_key="secret", client=client,
            sleeper=self.delays.append,
        )
        with self.assertRaises(OpenAICompatibleCapabilityError) as caught:
            chat.complete_json(self.messages, schema_name="s", schema=self.schema)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(len(client.calls), 1)
        self.assertIn("json_schema", client.calls[0][1]["json"]["response_format"]["type"])

    def test_retries_only_transient_status_and_transport_errors(self):
        client = FakeClient([FakeResponse(429), FakeResponse(503), FakeResponse()])
        chat = OpenAICompatibleChat(
            endpoint_url=self.endpoint, model_id="m", api_key="k", client=client,
            sleeper=self.delays.append,
        )
        self.assertEqual(chat.complete_json(self.messages, schema_name="s", schema=self.schema), {"answer": "ok"})
        self.assertEqual(len(client.calls), MAX_ATTEMPTS)
        self.assertEqual(self.delays, [0.2, 0.4])

        client = FakeClient([httpx.ConnectError("secret detail"), FakeResponse()])
        chat = OpenAICompatibleChat(
            endpoint_url=self.endpoint, model_id="m", api_key="k", client=client,
            sleeper=self.delays.append,
        )
        self.assertEqual(chat.complete_json(self.messages, schema_name="s", schema=self.schema), {"answer": "ok"})
        self.assertEqual(self.delays[-1], 0.2)

    def test_nontransient_failure_is_sanitized_without_retry(self):
        client = FakeClient([FakeResponse(401)])
        chat = OpenAICompatibleChat(
            endpoint_url=self.endpoint, model_id="m", api_key="very-secret", client=client,
            sleeper=self.delays.append,
        )
        with self.assertRaises(OpenAICompatibleError) as caught:
            chat.complete_json(self.messages, schema_name="s", schema=self.schema)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.delays, [])

    def test_exhausted_transient_failures_are_bounded_and_sanitized(self):
        client = FakeClient([FakeResponse(500), FakeResponse(502), FakeResponse(429)])
        chat = OpenAICompatibleChat(
            endpoint_url=self.endpoint, model_id="m", api_key="secret", client=client,
            sleeper=self.delays.append,
        )
        with self.assertRaisesRegex(OpenAICompatibleError, "temporarily unavailable") as caught:
            chat.complete_json(self.messages, schema_name="s", schema=self.schema)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(len(client.calls), MAX_ATTEMPTS)

    def test_invalid_json_and_incomplete_response_are_rejected(self):
        for response in (FakeResponse(content="not json"), FakeResponse(finish="length")):
            chat = OpenAICompatibleChat(
                endpoint_url=self.endpoint, model_id="m", api_key="k", client=FakeClient([response])
            )
            with self.assertRaises(OpenAICompatibleResponseError):
                chat.complete_json(self.messages, schema_name="s", schema=self.schema)

    def test_injected_client_is_not_closed_by_adapter(self):
        self.chat.close()
        self.assertFalse(self.client.closed)

    def test_request_bounds_are_checked_before_network_call(self):
        for kwargs in (
            {"schema_name": "s", "max_tokens": 0},
            {"schema_name": "s", "max_tokens": 4097},
            {"schema_name": "", "max_tokens": 1},
        ):
            with self.assertRaises(ValueError):
                self.chat.complete_json(self.messages, schema=self.schema, **kwargs)
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
