"""Offline contract tests for private, bounded R2 storage."""

from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import SecretStr

from app.integrations.r2 import (
    MAX_OBJECT_BYTES,
    R2ConfigurationError,
    R2Storage,
    R2StorageError,
)


def configured_settings() -> SimpleNamespace:
    return SimpleNamespace(
        R2_ACCOUNT_ID="account",
        R2_BUCKET="private-bucket",
        R2_ACCESS_KEY_ID=SecretStr("access"),
        R2_SECRET_ACCESS_KEY=SecretStr("secret"),
    )


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.response_body = BytesIO(b"file bytes")
        self.error: Exception | None = None

    def put_object(self, **kwargs) -> None:
        self.calls.append(("put_object", kwargs))
        if self.error:
            raise self.error

    def get_object(self, **kwargs) -> dict:
        self.calls.append(("get_object", kwargs))
        if self.error:
            raise self.error
        return {"Body": self.response_body}

    def delete_object(self, **kwargs) -> None:
        self.calls.append(("delete_object", kwargs))
        if self.error:
            raise self.error


class R2StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.storage = R2Storage(configured_settings(), client=self.client)

    def test_upload_passes_private_bucket_content_type_and_bytes(self) -> None:
        self.storage.upload("org/o/sources/s/versions/v/original", b"hello", "text/markdown")
        operation, request = self.client.calls[0]
        self.assertEqual(operation, "put_object")
        self.assertEqual(request["Bucket"], "private-bucket")
        self.assertEqual(request["Body"], b"hello")
        self.assertEqual(request["ContentLength"], 5)
        self.assertEqual(request["ContentType"], "text/markdown")
        self.assertNotIn("ACL", request)

    def test_upload_rejects_oversized_body_before_client_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "2 MiB"):
            self.storage.upload("key", b"x" * (MAX_OBJECT_BYTES + 1), "text/plain")
        self.assertEqual(self.client.calls, [])

    def test_download_is_bounded_and_closes_body(self) -> None:
        self.assertEqual(self.storage.download("key"), b"file bytes")
        self.assertTrue(self.client.response_body.closed)
        self.assertEqual(self.client.calls[0], ("get_object", {"Bucket": "private-bucket", "Key": "key"}))

    def test_download_rejects_oversized_object_and_closes_body(self) -> None:
        self.client.response_body = BytesIO(b"x" * (MAX_OBJECT_BYTES + 1))
        with self.assertRaisesRegex(R2StorageError, "2 MiB"):
            self.storage.download("key")
        self.assertTrue(self.client.response_body.closed)

    def test_delete_uses_private_bucket(self) -> None:
        self.storage.delete("key")
        self.assertEqual(self.client.calls[0], ("delete_object", {"Bucket": "private-bucket", "Key": "key"}))

    def test_client_failures_are_sanitized(self) -> None:
        self.client.error = RuntimeError("secret=access object=private-key")
        for operation in (
            lambda: self.storage.upload("key", b"data", "text/plain"),
            lambda: self.storage.download("key"),
            lambda: self.storage.delete("key"),
        ):
            with self.assertRaises(R2StorageError) as error:
                operation()
            self.assertNotIn("access", str(error.exception))
            self.assertNotIn("private-key", str(error.exception))

    def test_missing_bucket_is_rejected_with_injected_client(self) -> None:
        settings = configured_settings()
        settings.R2_BUCKET = ""
        with self.assertRaises(R2ConfigurationError):
            R2Storage(settings, client=self.client)

    def test_default_client_uses_endpoint_timeouts_and_retries(self) -> None:
        with patch("app.integrations.r2.boto3.client") as make_client:
            R2Storage(configured_settings())
        args = make_client.call_args.kwargs
        self.assertEqual(args["endpoint_url"], "https://account.r2.cloudflarestorage.com")
        self.assertEqual(args["region_name"], "auto")
        self.assertEqual(args["config"].connect_timeout, 3)
        self.assertEqual(args["config"].read_timeout, 10)
        self.assertEqual(args["config"].retries, {"mode": "standard", "total_max_attempts": 3})


if __name__ == "__main__":
    unittest.main()
