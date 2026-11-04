"""Deterministic exact-span chunks of NFC/LF-normalized guidance text."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass

HEADING = re.compile(r"(?m)^#{1,6}[ \t]+([^\n]+?)[ \t]*$")
PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")
SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])\s+")
WORD_BREAK = re.compile(r"\s+")
EMBEDDING_PREFIX = "passage: "


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_index: int
    title: str
    text: str
    start_offset: int
    end_offset: int


def _trim(source: str, start: int, end: int) -> tuple[int, int]:
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    return start, end


def _spans(source: str, start: int, end: int, separator: re.Pattern[str]) -> Iterator[tuple[int, int]]:
    cursor = start
    for match in separator.finditer(source, start, end):
        yield _trim(source, cursor, match.start())
        cursor = match.end()
    yield _trim(source, cursor, end)


def _sections(source: str, default_title: str) -> Iterator[tuple[str, int, int]]:
    headings = list(HEADING.finditer(source))
    if not headings:
        yield default_title, 0, len(source)
        return
    if headings[0].start() > 0:
        yield default_title, 0, headings[0].start()
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(source)
        yield heading.group(1).strip(), heading.end(), end


def chunk_text(
    normalized_text: str,
    *,
    count_tokens: Callable[[str], int],
    max_tokens: int = 384,
    default_title: str = "Untitled",
) -> list[Chunk]:
    """Return exact source slices with half-open Unicode code-point offsets.

    The caller normalizes input first and supplies the actual E5 tokenizer count,
    including special tokens. Its input includes the literal ``passage: `` prefix.
    No chunk is silently truncated. Raise if even one code point cannot fit.
    """
    if not isinstance(normalized_text, str):
        raise TypeError("normalized_text must be str")
    if normalized_text != unicodedata.normalize("NFC", normalized_text) or "\r" in normalized_text:
        raise ValueError("normalize guidance to NFC with LF line endings before chunking")
    if max_tokens < 2 or not default_title.strip():
        raise ValueError("invalid token budget or default title")

    def fits(start: int, end: int) -> bool:
        count = count_tokens(EMBEDDING_PREFIX + normalized_text[start:end])
        if not isinstance(count, int) or count < 0:
            raise ValueError("count_tokens must return a nonnegative integer")
        return count <= max_tokens

    output: list[Chunk] = []

    def emit(title: str, start: int, end: int) -> None:
        start, end = _trim(normalized_text, start, end)
        if start == end:
            return
        if not fits(start, end):
            raise ValueError("candidate chunk exceeds token budget")
        output.append(Chunk(len(output), title, normalized_text[start:end], start, end))

    def split_long(title: str, start: int, end: int, level: int) -> None:
        start, end = _trim(normalized_text, start, end)
        if start == end:
            return
        if fits(start, end):
            emit(title, start, end)
            return
        if level >= 2:
            cursor = start
            while cursor < end:
                # Token counts usually increase with length; verify every emitted span.
                lo, hi, best = cursor + 1, end, cursor
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if fits(cursor, mid):
                        best, lo = mid, mid + 1
                    else:
                        hi = mid - 1
                if best == cursor:
                    raise ValueError("token budget cannot fit one source code point")
                emit(title, cursor, best)
                cursor = best
            return
        separator = SENTENCE_BREAK if level == 0 else WORD_BREAK
        units = [(a, b) for a, b in _spans(normalized_text, start, end, separator) if a < b]
        if len(units) <= 1:
            split_long(title, start, end, level + 1)
            return
        pending_start, pending_end = units[0]
        for unit_start, unit_end in units[1:]:
            if fits(pending_start, unit_end):
                pending_end = unit_end
            else:
                split_long(title, pending_start, pending_end, level + 1)
                pending_start, pending_end = unit_start, unit_end
        split_long(title, pending_start, pending_end, level + 1)

    for title, section_start, section_end in _sections(normalized_text, default_title):
        paragraphs = [
            (a, b) for a, b in _spans(normalized_text, section_start, section_end, PARAGRAPH_BREAK) if a < b
        ]
        if not paragraphs:
            continue
        pending_start, pending_end = paragraphs[0]
        for paragraph_start, paragraph_end in paragraphs[1:]:
            if fits(pending_start, paragraph_end):
                pending_end = paragraph_end
            else:
                split_long(title, pending_start, pending_end, 0)
                pending_start, pending_end = paragraph_start, paragraph_end
        split_long(title, pending_start, pending_end, 0)
    if not output:
        raise ValueError("guidance contains no nonempty passages")
    return output
