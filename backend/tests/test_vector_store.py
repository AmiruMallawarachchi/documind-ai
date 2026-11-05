"""
tests/test_vector_store.py — The Pinecone service, driven by a recorded fake index.

Pinecone itself is not under test here; the two rules the module documents are:

  1. every read and delete carries an owner_id filter, because one missing filter is a cross-user leak
     rather than a cosmetic bug, and
  2. re-indexing deletes first, so a re-upload cannot leave a stale vector behind to be cited.

Both are observable from the calls the service makes, so a fake index that records them is enough —
no API key, no network, and the assertions survive a Pinecone SDK upgrade.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import vector_store


class FakeIndex:
    """Records every call and replays canned query matches."""

    def __init__(self, *, matches=None, query_matches=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.matches = list(matches or [])
        # A separate list for the existence probe inside delete_document_vectors, so a test can say
        # "there are vectors" for the delete path and something else for the search path.
        self.query_matches = query_matches
        self.upserted: list[list[dict]] = []
        self.deleted_filters: list[dict] = []

    def upsert(self, *, vectors):
        self.calls.append(("upsert", {"count": len(vectors)}))
        self.upserted.append(vectors)

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        chosen = self.query_matches if self.query_matches is not None else self.matches
        return SimpleNamespace(matches=chosen)

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        self.deleted_filters.append(kwargs["filters"])

    def describe_index_stats(self):
        self.calls.append(("stats", {}))
        return SimpleNamespace(total_vector_count=7, namespaces={"ns": {"vector_count": 7}})


def match(*, chunk_id="doc-1_0000", score=0.5, metadata=None):
    return SimpleNamespace(id=chunk_id, score=score, metadata=metadata if metadata is not None else {
        "chunk_text": "The fee is 250 dollars.",
        "document_id": "doc-1",
        "filename": "contract.pdf",
        "page_number": 2,
    })


@pytest.fixture
def use_index(monkeypatch) -> FakeIndex:
    index = FakeIndex()
    monkeypatch.setattr(vector_store, "get_index", lambda: index)
    return index


# ── ids ────────────────────────────────────────────────────────────────────────


def test_a_vector_id_is_padded_so_a_reupload_addresses_the_same_vector():
    assert vector_store.vector_id("doc-1", 0) == "doc-1_0000"
    assert vector_store.vector_id("doc-1", 42) == "doc-1_0042"
    assert vector_store.vector_id("doc-1", 42) < vector_store.vector_id("doc-1", 100)


# ── query_similar ──────────────────────────────────────────────────────────────


def test_a_search_always_filters_on_the_owner(use_index):
    """The one test not to delete: an unscoped query answers from somebody else's documents."""
    use_index.matches = [match()]

    results = vector_store.query_similar(owner_id="owner-a", query_vector=[0.1] * 3)

    assert use_index.calls[0][1]["filters"] == {"owner_id": "owner-a"}
    assert results[0].chunk_text == "The fee is 250 dollars."


def test_a_document_scope_becomes_an_in_filter(use_index):
    vector_store.query_similar(owner_id="owner-a", query_vector=[0.1], document_ids=["doc-1", "doc-2"])

    assert use_index.calls[0][1]["filters"] == {
        "owner_id": "owner-a",
        "document_id": {"$in": ["doc-1", "doc-2"]},
    }


def test_a_below_floor_match_is_dropped(use_index):
    """A weak chunk is not evidence; handing it to the model buys a confidently wrong citation."""
    use_index.matches = [match(score=0.55), match(chunk_id="doc-1_0001", score=0.04)]

    results = vector_store.query_similar(owner_id="owner-a", query_vector=[0.1], score_threshold=0.18)

    assert [c.chunk_id for c in results] == ["doc-1_0000"]


def test_a_match_without_text_is_skipped_rather_than_answered_with(use_index):
    use_index.matches = [match(metadata={"document_id": "doc-1", "filename": "a.pdf"})]

    assert vector_store.query_similar(owner_id="owner-a", query_vector=[0.1]) == []


def test_a_match_with_incomplete_metadata_still_becomes_a_readable_chunk(use_index):
    use_index.matches = [match(metadata={"chunk_text": "Orphan text."})]

    results = vector_store.query_similar(owner_id="owner-a", query_vector=[0.1])

    assert results[0].document_id == ""
    assert results[0].filename == "unknown"
    assert results[0].page_number == 0


def test_an_empty_response_is_an_empty_result_not_an_error(use_index):
    use_index.matches = []

    assert vector_store.query_similar(owner_id="owner-a", query_vector=[0.1]) == []


# ── upsert ─────────────────────────────────────────────────────────────────────


def test_upserting_a_document_deletes_its_previous_vectors_first(use_index):
    """Re-indexing must not leave a stale chunk retrievable, and delete-before-write is how."""
    use_index.query_matches = [match()]  # pretend the document is already indexed

    vector_store.upsert_chunks(
        document_id="doc-1",
        owner_id="owner-a",
        filename="contract.pdf",
        vectors=[("doc-1_0000", [0.1, 0.2])],
        metadatas=[{"chunk_text": "The fee is 250 dollars.", "document_id": "doc-1", "page_number": 2}],
    )

    order = [name for name, _ in use_index.calls]
    assert order == ["query", "delete", "upsert"]
    assert use_index.deleted_filters[0] == {"owner_id": "owner-a", "document_id": "doc-1"}


def test_an_owner_filter_wraps_every_written_vector(use_index):
    use_index.query_matches = []

    vector_store.upsert_chunks(
        document_id="doc-1",
        owner_id="owner-a",
        filename="contract.pdf",
        vectors=[("doc-1_0000", [0.1])],
        metadatas=[{"chunk_text": "Terms.", "document_id": "doc-1"}],
    )

    written = use_index.upserted[0][0]
    assert written["metadata"]["owner_id"] == "owner-a"


def test_metadata_that_pinecone_would_reject_is_dropped_not_sent(use_index):
    """Pinecone accepts flat str/int/float/bool only; a None or a list fails the whole batch."""
    use_index.query_matches = []

    vector_store.upsert_chunks(
        document_id="doc-1",
        owner_id="owner-a",
        filename="contract.pdf",
        vectors=[("doc-1_0000", [0.1])],
        metadatas=[
            {
                "chunk_text": "Terms.",
                "tags": ["a", "b"],
                "maybe": None,
                "page_number": 3,
                "is_scanned": True,
            }
        ],
    )

    written = use_index.upserted[0][0]["metadata"]
    assert set(written) == {"chunk_text", "page_number", "is_scanned", "owner_id"}
    assert written["page_number"] == 3


def test_a_write_is_batched(use_index, monkeypatch):
    monkeypatch.setattr(vector_store, "_UPSERT_BATCH_SIZE", 2)
    use_index.query_matches = []

    written = vector_store.upsert_chunks(
        document_id="doc-1",
        owner_id="owner-a",
        filename="big.pdf",
        vectors=[(f"doc-1_{i:04d}", [0.1]) for i in range(5)],
        metadatas=[{"chunk_text": f"Chunk {i}."} for i in range(5)],
    )

    assert written == 5
    assert [len(batch) for batch in use_index.upserted] == [2, 2, 1]


def test_writing_nothing_costs_nothing(use_index):
    """No delete, no query, no write units spent — and zero is the honest return value."""
    assert vector_store.upsert_chunks(
        document_id="doc-1", owner_id="owner-a", filename="empty.pdf", vectors=[], metadatas=[]
    ) == 0
    assert use_index.calls == []


# ── delete ─────────────────────────────────────────────────────────────────────


def test_deleting_a_document_that_has_no_vectors_skips_the_request(use_index):
    """A delete on an empty namespace still bills a write unit on the Starter tier."""
    use_index.query_matches = []

    assert vector_store.delete_document_vectors(document_id="doc-1", owner_id="owner-a") == 0
    assert [name for name, _ in use_index.calls] == ["query"]


def test_a_delete_is_scoped_to_the_owner_as_well_as_the_document(use_index):
    use_index.query_matches = [match()]

    assert vector_store.delete_document_vectors(document_id="doc-1", owner_id="owner-a") == 1
    assert use_index.deleted_filters[0] == {"owner_id": "owner-a", "document_id": "doc-1"}


def test_the_delete_probe_uses_a_vector_the_width_of_the_index(use_index, monkeypatch):
    monkeypatch.setattr(settings, "embedding_dimension", 4)
    use_index.query_matches = []

    vector_store.delete_document_vectors(document_id="doc-1", owner_id="owner-a")

    assert use_index.calls[0][1]["vector"] == [0.0] * 4


# ── health statistics ──────────────────────────────────────────────────────────


def test_statistics_degrade_instead_of_crashing_the_health_endpoint(use_index, monkeypatch):
    def explode():
        raise RuntimeError("pinecone unreachable")

    monkeypatch.setattr(vector_store, "get_index", explode)

    stats = vector_store.index_statistics()

    assert stats["total_vector_count"] is None
    assert "pinecone unreachable" in stats["error"]


def test_statistics_report_the_vector_count(use_index):
    assert vector_store.index_statistics() == {"total_vector_count": 7, "namespaces": ["ns"]}
