"""
services/chunking.py — Split extracted text into retrieval-sized units.

This is the file that most determines answer quality. Embedding models are tuned for
sentence-to-paragraph semantics: a 5,000-character chunk averages many topics into one
vector and matches none of them well, while a 50-character chunk loses the context that
makes it meaningful. Chunking is where "garbage in, garbage out" is actually decided.

Strategy: sentence-boundary packing with sentence-level overlap.

  1. Walk the document sentence by sentence, never mid-sentence.
  2. Pack sentences until the next one would cross chunk_max_characters.
  3. Seed the next chunk with the previous chunk's trailing sentences up to
     chunk_overlap_characters, so a fact straddling a boundary is retrievable from either side.
  4. Absorb any chunk left smaller than the minimum into its neighbour rather than indexing a
     fragment that only produces noise matches.
  5. Track the page each chunk starts on, because the citation must be truthful.

Pure functions, no I/O, no model calls — unit-testable in milliseconds without Pinecone,
Mongo, or an API key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.services.text_extractor import PageSegment

# Require a capital or quote after the break, so the splitter does not fracture
# "Dr. Ahmed" or "e.g. this" at what is an abbreviation period, not a sentence end.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")

# Chunks below this fraction of the cap get merged into the previous chunk.
_MIN_CHUNK_FRACTION = 0.2

_ABBREVIATIONS = (
    "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "no.", "vol.", "pp.",
    "e.g.", "i.e.", "etc.", "vs.", "u.s.",
    "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sept.",
    "oct.", "nov.", "dec.",
    "mon.", "tue.", "wed.", "thu.", "fri.", "sat.", "sun.",
)


@dataclass(frozen=True)
class Chunk:
    """One retrieval unit, ready to be embedded and upserted."""

    chunk_index: int
    text: str
    page_number: int
    char_count: int

    @property
    def token_estimate(self) -> int:
        """Cheap token proxy (~4 chars per English token), for logging only.

        The real budget is decided by the model tokenizer at inference time, so nothing
        correctness-critical reads this.
        """
        return max(1, round(self.char_count / 4))


def _split_sentences(text: str) -> list[str]:
    """Break text into sentences, honouring paragraph breaks and abbreviations."""
    sentences: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for piece in _SENTENCE_BREAK.split(paragraph):
            piece = piece.strip()
            if not piece:
                continue
            # A break right after a known abbreviation was a false positive: glue it back
            # onto the previous sentence rather than starting a new one.
            if sentences and sentences[-1].lower().endswith(_ABBREVIATIONS):
                sentences[-1] = f"{sentences[-1]} {piece}"
            else:
                sentences.append(piece)
    return sentences


def _hard_split(sentence: str, limit: int) -> list[str]:
    """Split an oversized single sentence on word boundaries.

    A table row or run-on clause can exceed the cap by itself. Word-boundary slicing keeps it
    retrievable; a fixed character offset would cut words in half.

    The trailing fall-through matters: a base64 blob or long hash has no word boundary at all, so
    word-based splitting alone could hand back a piece larger than `limit`. Since `limit` is what
    keeps a chunk inside the embedding model's context window and the LLM prompt budget, it has to be
    a hard guarantee, not a best effort.
    """
    if len(sentence) <= limit:
        return [sentence]

    pieces: list[str] = []
    current: list[str] = []
    length = 0
    for word in sentence.split(" "):
        if len(word) > limit:
            # An unsplittable word: flush what we have, then slice this one to size.
            if current:
                pieces.append(" ".join(current))
                current, length = [], 0
            for start in range(0, len(word), limit):
                pieces.append(word[start : start + limit])
            continue

        # +1 is the joining space.
        if current and length + len(word) + 1 > limit:
            pieces.append(" ".join(current))
            current, length = [word], len(word)
        else:
            length += len(word) + 1 if current else len(word)
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return [piece for piece in pieces if piece]


def _carry_overlap(sentences: list[str], overlap_limit: int) -> list[str]:
    """Take trailing whole sentences as the seed of the next chunk.

    Overlap is measured in sentences, not raw characters: slicing mid-sentence would inject a
    fragment that reads badly and drags an irrelevant vector toward the boundary.
    """
    if overlap_limit <= 0 or not sentences:
        return []
    carried: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        extra = len(sentence) + (1 if carried else 0)
        if total + extra > overlap_limit:
            break
        carried.insert(0, sentence)
        total += extra
    # If the overlap swallowed the whole chunk, progress would stall forever.
    if len(carried) >= len(sentences):
        return carried[1:] if len(carried) > 1 else []
    return carried


def chunk_segments(
    segments: list[PageSegment],
    max_characters: int | None = None,
    overlap_characters: int | None = None,
) -> list[Chunk]:
    """Pack page-tagged segments into overlapping, sentence-aligned chunks.

    Settings supply the defaults so tuning lives in one place, while a per-call override stays
    available for experiments and tests.
    """
    limit = max_characters or settings.chunk_max_characters
    overlap = (
        settings.chunk_overlap_characters if overlap_characters is None else overlap_characters
    )
    min_size = max(1, int(limit * _MIN_CHUNK_FRACTION))
    # An overlap at or above the cap would mean a chunk that never advances.
    overlap = min(overlap, max(0, limit - 1))

    # Flatten to (page_number, sentence) pairs so page identity travels with every sentence and
    # a chunk can be labelled by the page its first sentence actually came from.
    units: list[tuple[int, str]] = []
    for segment in segments:
        for sentence in _split_sentences(segment.text):
            if len(sentence) <= limit:
                units.append((segment.page_number, sentence))
            else:
                for piece in _hard_split(sentence, limit):
                    units.append((segment.page_number, piece))

    chunks: list[Chunk] = []

    def emit(items: list[tuple[int, str]]) -> None:
        text = " ".join(sentence for _, sentence in items).strip()
        if text:
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    text=text,
                    page_number=items[0][0],
                    char_count=len(text),
                )
            )

    buffer: list[tuple[int, str]] = []
    buffer_length = 0

    # Exactly one unit is consumed per loop iteration, so this always terminates — even when the
    # overlap seed fills a chunk.
    for page_number, sentence in units:
        extra = len(sentence) + (1 if buffer else 0)
        if buffer and buffer_length + extra > limit:
            emit(buffer)
            carried = _carry_overlap([text for _, text in buffer], overlap)
            page_by_sentence: dict[str, int] = {}
            for seen_page, text in buffer:
                page_by_sentence.setdefault(text, seen_page)
            buffer = [(page_by_sentence[text], text) for text in carried]
            buffer_length = sum(len(text) + 1 for _, text in buffer)
        buffer.append((page_number, sentence))
        buffer_length += extra

    emit(buffer)

    return _absorb_undersized(chunks, min_size)


def _absorb_undersized(chunks: list[Chunk], min_size: int) -> list[Chunk]:
    """Fold tiny chunks into the previous chunk instead of indexing noise.

    A 40-character chunk usually comes from a page tail. Embedding it separately yields a vector
    with almost no signal that still competes for every top-k slot, which measurably pushes
    genuinely relevant chunks out of the retrieval window.
    """
    merged: list[Chunk] = []
    for chunk in chunks:
        if merged and chunk.char_count < min_size:
            previous = merged[-1]
            combined = f"{previous.text} {chunk.text}".strip()
            merged[-1] = Chunk(
                chunk_index=previous.chunk_index,
                text=combined,
                page_number=previous.page_number,
                char_count=len(combined),
            )
            continue
        merged.append(chunk)

    # chunk_index must stay contiguous after merging, or IDs drift between runs.
    return [
        Chunk(
            chunk_index=position,
            text=chunk.text,
            page_number=chunk.page_number,
            char_count=chunk.char_count,
        )
        for position, chunk in enumerate(merged)
    ]
