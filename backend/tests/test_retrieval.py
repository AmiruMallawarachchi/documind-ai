"""
tests/test_retrieval.py — The evidence-shaping rules, tested without a vector database.

Retrieval decides what the model is allowed to know, so its tests are about the contract between the
chunks and the citations the user clicks:

  * the numbering in the prompt and the numbering in the UI must be the same number, and
  * an owner's question must never be answered from another owner's text, and
  * weak, duplicate, or unlabelled matches are dropped rather than padded in.

All of that is pure logic over a list of chunks, so it is tested directly instead of through the HTTP
API — no Pinecone, no embeddings, no credentials.
"""

from __future__ import annotations

import pytest

from app.models.document import RetrievedChunk
from app.services import retrieval


def chunk(
    *,
    chunk_id: str = "doc-1_0000",
    document_id: str = "doc-1",
    filename: str = "contract.pdf",
    page_number: int = 2,
    text: str = "The fee is 250 dollars.",
    score: float = 0.42,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        filename=filename,
        page_number=page_number,
        chunk_text=text,
        score=score,
    )


# ── dedupe ─────────────────────────────────────────────────────────────────────


def test_an_exact_repeat_only_keeps_the_higher_scoring_copy():
    """Overlap seeding means a boundary sentence can land in two consecutive chunks."""
    best_first = chunk(chunk_id="a", text="Clause 4 governs late payment.", score=0.61)
    repeat = chunk(chunk_id="b", text="Clause 4 governs late payment.", score=0.40)

    assert [c.chunk_id for c in retrieval._deduplicate([best_first, repeat])] == ["a"]


def test_whitespace_differences_do_not_hide_a_duplicate():
    """Compared on normalised text, so a trailing newline is the same passage."""
    one = chunk(chunk_id="a", text="  Annual   fee is 250.\n")
    two = chunk(chunk_id="b", text="Annual fee is 250. ")

    assert len(retrieval._deduplicate([one, two])) == 1


def test_distinct_chunks_survive_deduplication():
    assert len(retrieval._deduplicate([chunk(chunk_id="a", text="One."), chunk(chunk_id="b", text="Two.")])) == 2


# ── retrieve ───────────────────────────────────────────────────────────────────


def test_a_blank_question_returns_no_evidence_without_calling_the_embedder(monkeypatch):
    """The cheap guard runs before the model: an empty string should not cost an embedding."""
    calls = []
    monkeypatch.setattr(retrieval, "embed_query", lambda text: calls.append(text) or [0.0])

    assert retrieval.retrieve(owner_id="owner-a", question="   ") == []
    assert calls == []


def test_the_owner_scope_reaches_the_vector_query(monkeypatch):
    """The leak test that matters: owner_id travels into the store call, not just into the log line.

    Every call is recorded rather than the last one, because a narrow empty scope now makes a second,
    unfiltered attempt — and an owner filter silently missing from *that* one would be the leak.
    """
    calls: list[dict] = []

    def fake_query_similar(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(retrieval, "query_similar", fake_query_similar)
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.1, 0.2])

    retrieval.retrieve(owner_id="owner-a", question="fee?", document_ids=["doc-1"], top_k=3)

    assert calls, "the store was never asked"
    for call in calls:
        assert call["owner_id"] == "owner-a"
        assert call["document_ids"] == ["doc-1"]
        assert call["top_k"] == 3
    assert calls[0]["score_threshold"] == retrieval.DEFAULT_SCORE_FLOOR


def test_retrieved_duplicates_are_collapsed_before_the_prompt_sees_them(monkeypatch):
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        retrieval,
        "query_similar",
        lambda **kwargs: [
            chunk(chunk_id="a", text="Same text.", score=0.7),
            chunk(chunk_id="b", text="Same text.", score=0.5),
            chunk(chunk_id="c", text="Different text.", score=0.4),
        ],
    )

    results = retrieval.retrieve(owner_id="owner-a", question="fee?")

    assert [c.chunk_id for c in results] == ["a", "c"]


# ── the overview fallback ─────────────────────────────────────────────────────
#
# "What is this document?" asks about a file as a whole, so it shares no vocabulary with any passage
# inside it. Measured against the very document it names, cosine sits at 0.08 and the floor calls that
# noise — which routes the question to the honest-looking "I could not find anything" instead of a
# summary. These tests hold the narrow-scope exception and, just as importantly, its boundary.


def _store(*matches):
    """A vector-store stub that applies the threshold it is handed, the way `query_similar` does.

    Faithful on this one point because it is the whole subject: a fake that ignored `score_threshold`
    would make every fallback test below pass for the wrong reason, and the ordering of the retry would
    never be exercised. Records the threshold of each call.
    """
    calls: list = []

    def fake(**kwargs):
        floor = kwargs["score_threshold"]
        calls.append(floor)
        return [match for match in matches if floor is None or match.score >= floor]

    return fake, calls


# Two passages from the middle of a file, both scoring the way a whole-document question does.
_OVERVIEW_MATCHES = (
    chunk(chunk_id="a", text="Q3 Vendor Agreement. Late fee: 2.5 percent.", score=0.08),
    chunk(chunk_id="b", text="Termination requires thirty days notice.", score=0.05),
)


def test_a_whole_document_question_falls_back_to_unfiltered_ranking(monkeypatch):
    fake, calls = _store(*_OVERVIEW_MATCHES)
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retrieval, "query_similar", fake)

    results = retrieval.retrieve(
        owner_id="owner-a", question="what is this document ?", document_ids=["doc-1"]
    )

    assert calls == [retrieval.DEFAULT_SCORE_FLOOR, None], "the retry should drop the floor, not raise it"
    assert [c.chunk_id for c in results] == ["a", "b"]


def test_a_search_with_hits_does_not_spend_the_second_query(monkeypatch):
    """The floor still decides the normal case; the retry only happens when it produced nothing."""
    fake, calls = _store(chunk(chunk_id="a", text="The late fee is 250 dollars.", score=0.56))
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retrieval, "query_similar", fake)

    results = retrieval.retrieve(owner_id="owner-a", question="what is the late fee?", document_ids=["doc-1"])

    assert calls == [retrieval.DEFAULT_SCORE_FLOOR], "a search that landed must not re-query"
    assert [c.chunk_id for c in results] == ["a"]


@pytest.mark.parametrize("scope", [["doc-1"], ["doc-1", "doc-2"], ["doc-1", "doc-2", "doc-3"]])
def test_a_narrow_scope_gets_the_fallback(monkeypatch, scope):
    fake, calls = _store(*_OVERVIEW_MATCHES)
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retrieval, "query_similar", fake)

    assert retrieval.retrieve(owner_id="owner-a", question="summarise it", document_ids=scope)

    assert len(calls) == 2


def test_a_wide_scope_does_not_get_the_fallback(monkeypatch):
    """With a library in scope, zero hits really does mean "not in your documents".

    Lowering the floor here would paste the nearest few hundred chunks into the prompt and buy a fluent
    answer sourced from irrelevant text, which is the failure the floor exists to prevent.
    """
    fake, calls = _store(*_OVERVIEW_MATCHES)
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retrieval, "query_similar", fake)

    scope = [f"doc-{index}" for index in range(retrieval._OVERVIEW_SCOPE_MAX_DOCUMENTS + 1)]
    results = retrieval.retrieve(owner_id="owner-a", question="summarise everything", document_ids=scope)

    assert calls == [retrieval.DEFAULT_SCORE_FLOOR], "a wide scope must not re-query without the floor"
    assert results == []


def test_an_unscoped_search_does_not_get_the_fallback(monkeypatch):
    """`document_ids=None` means the whole corpus, so the narrow-scope rule cannot apply to it."""
    fake, calls = _store(*_OVERVIEW_MATCHES)
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retrieval, "query_similar", fake)

    assert retrieval.retrieve(owner_id="owner-a", question="what is this document ?") == []
    assert calls == [retrieval.DEFAULT_SCORE_FLOOR]


def test_the_fallback_invents_nothing_when_the_scope_is_empty(monkeypatch):
    """A scope with no vectors is empty twice over; the retry must not manufacture evidence."""
    fake, calls = _store()
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retrieval, "query_similar", fake)

    results = retrieval.retrieve(owner_id="owner-a", question="what is this document ?", document_ids=["doc-1"])

    assert results == []
    assert len(calls) == 2, "the retry still ran, and found what the first query found: nothing"


def test_the_fallback_collapses_duplicates_like_the_first_query(monkeypatch):
    """The retry is not a second-class path: overlap seeding is still deduplicated before the prompt."""
    fake, _ = _store(
        _OVERVIEW_MATCHES[0],
        chunk(chunk_id="dup", text="Q3 Vendor Agreement. Late fee: 2.5 percent.", score=0.07),
        _OVERVIEW_MATCHES[1],
    )
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retrieval, "query_similar", fake)

    results = retrieval.retrieve(owner_id="owner-a", question="what is this document ?", document_ids=["doc-1"])

    assert [c.chunk_id for c in results] == ["a", "b"]


# ── context block and citations ────────────────────────────────────────────────


def test_the_prompt_markers_and_the_citations_agree_on_the_numbering():
    """A citation off by one is worse than no citation: it points at a passage that does not support
    the claim. Both come from the same list in the same order, and this is the test that keeps it true."""
    chunks = [
        chunk(chunk_id="a", text="First passage.", page_number=1),
        chunk(chunk_id="b", text="Second passage.", page_number=3),
        chunk(chunk_id="c", text="Third passage.", page_number=7),
    ]

    block = retrieval.build_context_block(chunks)
    citations = retrieval.citations_from(chunks)

    assert [c.index for c in citations] == [1, 2, 3]
    assert citations[-1].page_number == 7
    for position, cite in enumerate(citations, start=1):
        assert f"[{position}] (source: {cite.filename}, page {cite.page_number})" in block


def test_the_context_block_numbers_sources_and_carries_their_text():
    block = retrieval.build_context_block([chunk(text="The fee is 250 dollars.", page_number=4)])

    assert block.startswith("[1] (source: contract.pdf, page 4)")
    assert "The fee is 250 dollars." in block


def test_a_chunk_without_a_page_is_still_attributable():
    """TXT and DOCX extractions can have no page concept; the source label must not read "page 0"."""
    block = retrieval.build_context_block([chunk(text="Notes.", page_number=0)])

    assert "[1] (source: contract.pdf)" in block
    assert "page" not in block


def test_no_context_is_an_explicit_empty_block():
    """The phrase reaches the prompt, where the model reads it as "there is nothing to cite from"."""
    assert retrieval.build_context_block([]) == "(no retrieved context)"


def test_a_citation_snippet_is_truncated_for_the_tooltip_only():
    long_text = "x" * 900

    citation = retrieval.citations_from([chunk(text=long_text)])[0]

    assert len(citation.snippet) == 220
    assert citation.snippet == long_text[:220]


# ── filename backfill ──────────────────────────────────────────────────────────


def test_a_named_chunk_is_not_re_read_from_mongo(monkeypatch):
    lookups = []
    monkeypatch.setattr(retrieval.metadata_store, "filenames_for", lambda owner, ids: lookups.append(ids) or {})

    result = retrieval.resolve_filenames("owner-a", [chunk(filename="loan.pdf")])

    assert lookups == []
    assert result[0].filename == "loan.pdf"


def test_only_an_unknown_name_is_backfilled_and_the_rest_is_left_alone(monkeypatch):
    monkeypatch.setattr(
        retrieval.metadata_store,
        "filenames_for",
        lambda owner, ids: {"doc-2": "renewed.pdf"} if ids == ["doc-2"] else {},
    )
    chunks = [
        chunk(chunk_id="a", document_id="doc-1", filename="terms.pdf"),
        chunk(chunk_id="b", document_id="doc-2", filename="unknown"),
    ]

    result = retrieval.resolve_filenames("owner-a", chunks)

    assert [c.filename for c in result] == ["terms.pdf", "renewed.pdf"]


def test_a_metadata_store_that_answers_nothing_keeps_the_existing_labels(monkeypatch):
    """Atlas may be down or the row deleted. Losing an answer over a label is the wrong priority."""
    monkeypatch.setattr(retrieval.metadata_store, "filenames_for", lambda owner, ids: {})

    result = retrieval.resolve_filenames("owner-a", [chunk(filename="unknown")])

    assert result[0].filename == "unknown"


@pytest.mark.parametrize("missing", ["", "unknown"])
def test_both_stale_label_shapes_trigger_a_lookup(missing, monkeypatch):
    seen = []
    monkeypatch.setattr(
        retrieval.metadata_store, "filenames_for", lambda owner, ids: seen.append(ids) or {"doc-1": "found.pdf"}
    )

    result = retrieval.resolve_filenames("owner-a", [chunk(filename=missing)])

    assert seen == [["doc-1"]]
    assert result[0].filename == "found.pdf"
