"""Private, size-bounded Cloudflare R2 object storage adapter."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import Settings, get_settings

MAX_OBJECT_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3


class R2StorageError(RuntimeError):
    """A storage operation failed; its message is safe to expose to callers."""


class R2ConfigurationError(RuntimeError):
    """R2 credentials or bucket configuration is incomplete."""


class R2Storage:
    """Synchronous R2 adapter with injectable client for offline use and tests.

    Objects remain private: uploads never request an ACL and this adapter never
    creates or returns public or presigned URLs.
    """

    def __init__(self, settings: Settings | None = None, *, client: Any = None) -> None:
        self._settings = settings or get_settings()
        self._bucket = self._settings.R2_BUCKET
        if client is None:
            account_id = self._settings.R2_ACCOUNT_ID
            access_key = self._settings.R2_ACCESS_KEY_ID.get_secret_value()
            secret_key = self._settings.R2_SECRET_ACCESS_KEY.get_secret_value()
            if not all((account_id, self._bucket, access_key, secret_key)):
                raise R2ConfigurationError("R2 storage is not configured.")
            client = boto3.client(
                "s3",
                endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                region_name="auto",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=BotoConfig(
                    connect_timeout=CONNECT_TIMEOUT_SECONDS,
                    read_timeout=READ_TIMEOUT_SECONDS,
                    retries={"mode": "standard", "total_max_attempts": MAX_ATTEMPTS},
                    s3={"addressing_style": "path"},
                ),
            )
        elif not self._bucket:
            raise R2ConfigurationError("R2 bucket is not configured.")
        self._client = client

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key or "\x00" in key or len(key.encode("utf-8")) > 1024:
            raise ValueError("R2 object key is invalid.")

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        """Store at most 2 MiB in the private bucket with its media type."""
        self._validate_key(key)
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if len(data) > MAX_OBJECT_BYTES:
            raise ValueError("R2 object exceeds the 2 MiB limit.")
        if not isinstance(content_type, str) or not content_type.strip():
            raise ValueError("content_type is required.")
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentLength=len(data),
                ContentType=content_type,
            )
        except Exception:
            raise R2StorageError("The original file could not be stored.") from None

    def get_bytes(self, key: str, max_bytes: int = MAX_OBJECT_BYTES) -> bytes:
        """Read a private object, rejecting any response larger than 2 MiB."""
        self._validate_key(key)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= MAX_OBJECT_BYTES:
            raise ValueError("max_bytes must be between zero and 2 MiB.")
        body = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            content = body.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise R2StorageError("The stored file exceeds the 2 MiB limit.")
            return content
        except R2StorageError:
            raise
        except Exception:
            raise R2StorageError("The original file could not be retrieved.") from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass

    # Friendly aliases for callers that use storage-oriented naming.
    upload = put_bytes
    download = get_bytes

    def delete(self, key: str) -> None:
        """Delete an object from the private bucket."""
        self._validate_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception:
            raise R2StorageError("The original file could not be deleted.") from None
