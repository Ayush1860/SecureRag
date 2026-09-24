"""Token-aware recursive text splitter.

Splits on the coarsest boundary that fits: paragraph, then sentence, then word, then (for
pathological tokens) characters. Chunk length is measured with ``length_fn`` which should be
the embedding model's tokenizer so chunks never exceed what the encoder actually sees.
Chunks carry their character offsets into the source text; ``text`` is always
``source[start:end]`` so separators are preserved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

LengthFn = Callable[[str], int]

_PARAGRAPH_RE = re.compile(r"\S(?:.*?\S)?(?=\s*\n\s*\n|\s*$)", re.DOTALL)
_SENTENCE_RE = re.compile(r"\S.*?(?:[.!?](?=\s)|$)", re.DOTALL)
_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    start: int
    end: int
    tokens: int


def whitespace_length(text: str) -> int:
    """Fallback length function: whitespace-delimited word count."""
    return len(text.split())


def _spans(pattern: re.Pattern[str], text: str, start: int, end: int) -> list[tuple[int, int]]:
    return [(start + m.start(), start + m.end()) for m in pattern.finditer(text[start:end])]


def _char_split(text: str, start: int, end: int, max_tokens: int, length_fn: LengthFn) -> list[tuple[int, int]]:
    """Split one oversized token run into pieces that each fit ``max_tokens``."""
    out: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        step = end - pos
        while step > 1 and length_fn(text[pos:pos + step]) > max_tokens:
            step = max(1, step // 2)
        out.append((pos, pos + step))
        pos += step
    return out


def _atomic_units(text: str, max_tokens: int, length_fn: LengthFn) -> list[tuple[int, int, int]]:
    """Break ``text`` into (start, end, tokens) units that each fit ``max_tokens``."""
    levels = (_PARAGRAPH_RE, _SENTENCE_RE, _WORD_RE)
    units: list[tuple[int, int, int]] = []

    def descend(start: int, end: int, level: int) -> None:
        for s, e in _spans(levels[level], text, start, end):
            n = length_fn(text[s:e])
            if n <= max_tokens:
                units.append((s, e, n))
            elif level + 1 < len(levels):
                descend(s, e, level + 1)
            else:
                units.extend((cs, ce, length_fn(text[cs:ce])) for cs, ce in _char_split(text, s, e, max_tokens, length_fn))

    descend(0, len(text), 0)
    return units


def chunk_text(
    text: str,
    max_tokens: int = 400,
    overlap_tokens: int = 60,
    length_fn: LengthFn = whitespace_length,
) -> list[TextChunk]:
    """Split ``text`` into chunks of at most ``max_tokens`` with ~``overlap_tokens`` of overlap.

    Units (paragraphs, or sentences/words when a paragraph is too long) are packed greedily.
    Each new chunk starts with the trailing units of the previous chunk whose total length is
    at most ``overlap_tokens``, so overlap always falls on a sentence or word boundary.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if not 0 <= overlap_tokens < max_tokens:
        raise ValueError("overlap_tokens must be in [0, max_tokens)")

    units = _atomic_units(text, max_tokens, length_fn)
    chunks: list[TextChunk] = []
    current: list[tuple[int, int, int]] = []
    current_tokens = 0
    fresh = 0  # units in ``current`` that are not overlap carried from the previous chunk

    def emit() -> None:
        start, end = current[0][0], current[-1][1]
        chunks.append(TextChunk(index=len(chunks), text=text[start:end], start=start, end=end, tokens=current_tokens))

    for unit in units:
        if current and current_tokens + unit[2] > max_tokens:
            if fresh:
                emit()
            carried: list[tuple[int, int, int]] = []
            carried_tokens = 0
            for prev in reversed(current):
                if carried_tokens + prev[2] > overlap_tokens:
                    break
                carried.insert(0, prev)
                carried_tokens += prev[2]
            while carried and carried_tokens + unit[2] > max_tokens:
                carried_tokens -= carried.pop(0)[2]
            current, current_tokens, fresh = carried, carried_tokens, 0
        current.append(unit)
        current_tokens += unit[2]
        fresh += 1

    if current and fresh:
        emit()
    return chunks
