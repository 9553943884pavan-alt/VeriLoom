"""Password, opaque session, CSRF, and browser boundary primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

from app.core.config import Settings


SESSION_LIFETIME = timedelta(hours=24)
PRODUCTION_COOKIE = "__Host-veriloom_session"
LOCAL_COOKIE = "veriloom_session_dev"
PASSWORD_HASHER = PasswordHasher(type=Type.ID)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def _key(settings: Settings) -> bytes:
    return settings.SESSION_SECRET.get_secret_value().encode("utf-8")


def session_token_hash(settings: Settings, token: str) -> bytes:
    return hmac.digest(_key(settings), b"session:v1:" + token.encode("utf-8"), "sha256")


def csrf_token(settings: Settings, token: str) -> str:
    digest = hmac.digest(_key(settings), b"csrf:v1:" + token.encode("utf-8"), "sha256")
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def csrf_matches(settings: Settings, token: str, supplied: str | None) -> bool:
    return supplied is not None and hmac.compare_digest(csrf_token(settings, token), supplied)


def cookie_name(settings: Settings) -> str:
    return LOCAL_COOKIE if settings.APP_ORIGIN.startswith("http://localhost:") else PRODUCTION_COOKIE


def secure_cookie(settings: Settings) -> bool:
    return cookie_name(settings) == PRODUCTION_COOKIE


class FixedWindowRateLimiter:
    """Bounded per-process guard for the MVP's one API instance."""

    def __init__(self, *, max_keys: int = 4096) -> None:
        self._events: dict[str, deque[float]] = {}
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> int | None:
        """Return Retry-After seconds if blocked, otherwise record the request."""
        now = time.monotonic()
        with self._lock:
            if len(self._events) >= self._max_keys and key not in self._events:
                self._events = {
                    name: times
                    for name, times in self._events.items()
                    if times and times[-1] > now - window_seconds
                }
                if len(self._events) >= self._max_keys:
                    return window_seconds
            events = self._events.setdefault(key, deque())
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(events[0] + window_seconds - now) + 1)
            events.append(now)
            return None


AUTH_RATE_LIMITER = FixedWindowRateLimiter()
