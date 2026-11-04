"""Source upload validation must preserve the original audit hash and exact text."""

import hashlib

import pytest

from app.rag.ingestion import (
    MAX_SOURCE_BYTES,
    GuidanceTooLarge,
    InvalidGuidanceText,
    UnsupportedGuidanceFormat,
    extract_guidance,
)


def test_extracts_normalized_text_but_hashes_original_bytes() -> None:
    original = b"\xef\xbb\xbf# Brand\r\nCafe\xcc\x81\rRules\n"
    result = extract_guidance("brand.MD", original)
    assert result.text == "# Brand\nCaf\u00e9\nRules\n"
    assert result.sha256 == hashlib.sha256(original).hexdigest()
    assert result.byte_size == len(original)
    assert result.mime_type == "text/markdown"


@pytest.mark.parametrize("filename", ["image.png", "no_extension", ""])
def test_rejects_unsupported_names(filename: str) -> None:
    with pytest.raises(UnsupportedGuidanceFormat):
        extract_guidance(filename, b"Policy")


@pytest.mark.parametrize("original", [b"\xff", b"Policy\x00hidden"])
def test_rejects_non_text_bytes(original: bytes) -> None:
    with pytest.raises(UnsupportedGuidanceFormat):
        extract_guidance("rules.txt", original)


def test_rejects_oversize_before_decoding() -> None:
    with pytest.raises(GuidanceTooLarge):
        extract_guidance("rules.txt", b"a" * (MAX_SOURCE_BYTES + 1))


@pytest.mark.parametrize("original", [b"", b" \n\t "])
def test_rejects_empty_guidance(original: bytes) -> None:
    with pytest.raises(InvalidGuidanceText):
        extract_guidance("rules.txt", original)
