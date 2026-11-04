"""Validate and extract a supported text guidance upload before chunking."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath


MAX_SOURCE_BYTES = 2 * 1024 * 1024
SUPPORTED_TYPES = {".txt": "text/plain", ".md": "text/markdown"}


class UnsupportedGuidanceFormat(ValueError):
    """The file extension or bytes are not supported text guidance."""


class GuidanceTooLarge(ValueError):
    """The original upload exceeds the source size limit."""


class InvalidGuidanceText(ValueError):
    """The decoded source contains no usable guidance text."""


@dataclass(frozen=True, slots=True)
class ExtractedGuidance:
    text: str
    sha256: str
    byte_size: int
    mime_type: str


def extract_guidance(filename: str, original: bytes) -> ExtractedGuidance:
    """Hash original bytes and return NFC/LF text for exact-span chunking.

    MIME is inferred from the allowed extension after the bytes pass strict UTF-8
    decoding. The browser's Content-Type is never trusted as proof of file type.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise UnsupportedGuidanceFormat("a .txt or .md filename is required")
    extension = PurePath(filename).suffix.lower()
    if extension not in SUPPORTED_TYPES:
        raise UnsupportedGuidanceFormat("only .txt and .md guidance is supported")
    if not isinstance(original, bytes):
        raise TypeError("original must be bytes")
    if len(original) > MAX_SOURCE_BYTES:
        raise GuidanceTooLarge("guidance exceeds the 2 MiB limit")
    try:
        decoded = original.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnsupportedGuidanceFormat("guidance must be UTF-8 text") from exc
    if "\x00" in decoded:
        raise UnsupportedGuidanceFormat("guidance contains NUL bytes")
    normalized = unicodedata.normalize("NFC", decoded.replace("\r\n", "\n").replace("\r", "\n"))
    if not normalized.strip():
        raise InvalidGuidanceText("guidance contains no text")
    return ExtractedGuidance(
        text=normalized,
        sha256=hashlib.sha256(original).hexdigest(),
        byte_size=len(original),
        mime_type=SUPPORTED_TYPES[extension],
    )
