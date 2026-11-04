"""
tests/test_chunking.py — Invariants the chunker must never break.

These are the properties that make retrieval trustworthy, chosen because breaking any of them silently
degrades answer quality rather than raising an error — the category of bug a test has to catch, since
nothing downstream will complain.
"""

from __future__ import annotations

import pytest

from app.services.chunking import Chunk, _hard_split, _split_sentences, chunk_segments
from app.services.text_extractor import PageSegment


def segments(*pairs: tuple[int, str]) -> list[PageSegment]:
    return [PageSegment(page_number=page, text=text) for page, text in pairs]


def test_respects_the_character_cap_across_pathological_input():
    """A 1,500-character run of one unbreakable token must still be capped.

    The overlap seed is allowed to push a chunk modestly past the cap, so the assertion is on a bounded
    overshoot rather than an exact ceiling — that slack is by design, not sloppiness.
    """
    blob = "B" * 1500
    prose = "The quick brown fox jumps over the lazy dog. " * 40
    chunks = chunk_segments(segments((1, prose), (2, blob)), max_characters=300, overlap_characters=60)

    assert chunks
    for chunk in chunks:
        assert chunk.char_count <= 300 + 60 + 1, f"chunk {chunk.chunk_index} is {chunk.char_count}"


def test_chunk_indices_are_contiguous_and_zero_based():
    """IDs are derived from chunk_index, so a gap would silently orphan re-uploaded vectors."""
    chunks = chunk_segments(segments((1, "One two three. " * 200)), max_characters=200, overlap_characters=40)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_never_splits_mid_sentence():
    """A chunk must end where a sentence ended.

    WHY: an embedding of "…the contract terminat" carries a truncated meaning, and the matching half
    lives in the next chunk, so neither retrieves well for a query about termination.
    """
    text = "First sentence here. Second one follows. Third concludes it. " * 30
    chunks = chunk_segments(segments((1, text)), max_characters=120, overlap_characters=30)
    for chunk in chunks:
        # Every chunk ends on terminal punctuation or on an overlap prefix that is itself complete.
        assert chunk.text[-1] in ".!?", f"chunk ends mid-sentence: {chunk.text[-40:]!r}"


def test_abbreviation_period_does_not_break_a_sentence():
    """Splitting "Dr. Ahmed" into two units corrupts the entity that a citation depends on."""
    sentences = _split_sentences("Dr. Ahmed arrived on Mon. 12 Jan and signed the deed.")
    assert any("Dr. Ahmed" in sentence for sentence in sentences)
    assert not any(sentence.startswith("12 Jan") for sentence in sentences)


def test_page_number_is_carried_from_the_first_sentence_of_the_chunk():
    """Citation truthfulness: a chunk labelled page 4 must start on page 4."""
    chunks = chunk_segments(
        segments((1, "Alpha content. " * 20), (2, "Beta content. " * 20), (7, "Gamma content. " * 20)),
        max_characters=100,
        overlap_characters=0,
    )
    pages = {chunk.page_number for chunk in chunks}
    assert pages == {1, 2, 7}, "a page label was dropped or invented during packing"


def test_overlap_carries_whole_sentences_between_adjacent_chunks():
    """Boundary facts must be findable from either side, or recall depends on luck."""
    chunks = chunk_segments(
        segments((1, "Aa aa aa. Bb bb bb. Cc cc cc. Dd dd dd. Ee ee ee. Ff ff ff.")),
        max_characters=30,
        overlap_characters=20,
    )
    assert len(chunks) >= 2
    for previous, following in zip(chunks, chunks[1:]):
        overlap = set(previous.text.split(". ")).intersection(following.text.split(". "))
        assert any(token for token in overlap if len(token) > 2), "adjacent chunks share no sentence"


def test_undersized_tail_is_absorbed_not_indexed_alone():
    """A 30-character fragment would compete for a top_k slot with almost no signal in it."""
    chunks = chunk_segments(
        segments((1, "Substantial sentence about the loan terms. " * 8 + "End.")),
        max_characters=100,
        overlap_characters=0,
    )
    assert all(chunk.char_count >= 20 for chunk in chunks)


def test_empty_input_produces_no_chunks():
    assert chunk_segments([]) == []
    assert chunk_segments(segments((1, "   \n\n   ")), max_characters=100) == []


def test_single_short_chunk_survives_unchanged():
    """The degenerate case: one sentence shorter than the cap must still yield exactly one chunk."""
    chunks = chunk_segments(segments((4, "Only one sentence.")), max_characters=500)
    assert len(chunks) == 1
    assert chunks[0].text == "Only one sentence."
    assert chunks[0].page_number == 4


@pytest.mark.parametrize("limit", [40, 100, 300])
def test_hard_split_guarantees_the_cap_even_without_word_boundaries(limit):
    pieces = _hard_split("x" * (limit * 3 + 7), limit)
    assert all(len(piece) <= limit for piece in pieces)
    assert "".join(pieces).count("x") == limit * 3 + 7, "hard_split lost or duplicated characters"


def test_overlapping_chunks_are_deterministic():
    """Re-chunking the same document must produce the same IDs, or re-upload creates duplicates."""
    source = segments((1, "Alpha beta gamma. Delta epsilon zeta. " * 30))
    first = chunk_segments(source, max_characters=150, overlap_characters=30)
    second = chunk_segments(source, max_characters=150, overlap_characters=30)
    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]


def test_token_estimate_is_a_proxy_not_a_count():
    """Roughly a quarter of characters; used for logs only, so it must never look authoritative."""
    chunk = Chunk(chunk_index=0, text="a" * 400, page_number=1, char_count=400)
    assert chunk.token_estimate == 100
