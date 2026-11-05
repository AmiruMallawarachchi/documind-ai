"""
tests/test_ingestion.py — The parse → chunk → embed → index path, and how it reports failure.

run_ingestion is called directly rather than through HTTP here: the background-task plumbing is covered
in test_api.py, and what matters in this module is the pipeline's own contract — what lands in each
store and what status transitions happen when a stage blows up.
"""

from __future__ import annotations


from app.models.document import DocumentStatus
from app.services import ingestion
from app.services.vector_store import vector_id
from tests.test_extractor import build_pdf


def ingest(client_free, fake_metadata, fake_vectors, filename="loan.pdf", data=None):
    """Run the pipeline the way the background task would, and return the result plus the stored row."""
    document_id = ingestion.new_document_id()
    fake_metadata.create_document_record(
        document_id=document_id,
        owner_id="owner-a",
        filename=filename,
        content_type="application/pdf",
        size_bytes=len(data or b""),
    )
    result = ingestion.run_ingestion(
        document_id=document_id,
        owner_id="owner-a",
        filename=filename,
        data=data if data is not None else build_pdf(["The late fee is 250 dollars per occurrence."]),
    )
    return document_id, result, fake_metadata.documents[document_id]


def test_successful_ingestion_writes_both_stores(fake_metadata, fake_vectors, fake_embedding):
    document_id, result, row = ingest(None, fake_metadata, fake_vectors)

    assert result.indexed is True
    assert result.chunk_count == fake_vectors["upserted"][0]["count"] > 0
    assert row["status"] == DocumentStatus.indexed.value
    assert row["chunk_count"] == result.chunk_count


def test_vectors_carry_owner_id_and_chunk_text(fake_metadata, fake_vectors, fake_embedding):
    """Two rules the whole design depends on.

    owner_id in metadata is what makes cross-user leakage impossible at query time, and chunk_text in
    Pinecone is what keeps full document text out of the 512 MB Atlas cap while still allowing a
    citation to be rendered without a second lookup.
    """
    _, _, _ = ingest(None, fake_metadata, fake_vectors)
    written = fake_vectors["upserted"][0]

    for metadata in written["metadatas"]:
        assert metadata["owner_id"] == "owner-a"
        assert metadata["chunk_text"].strip()
        assert metadata["document_id"] == written["document_id"]
        assert isinstance(metadata["page_number"], int)


def test_vector_ids_are_deterministic_and_zero_padded(fake_metadata, fake_vectors, fake_embedding):
    """Re-uploading the same document must address the same ids, or Pinecone accumulates duplicates."""
    assert vector_id("abc", 0) == "abc_0000"
    assert vector_id("abc", 42) == "abc_0042"

    ingest(None, fake_metadata, fake_vectors)
    written = fake_vectors["upserted"][0]
    assert written["ids"] == [vector_id(written["document_id"], index) for index in range(written["count"])]


def test_embedding_dimension_matches_config(fake_metadata, fake_vectors, fake_embedding):
    """A wrong-dimension vector is what makes Pinecone reject every subsequent write."""
    from app.core.config import settings

    ingest(None, fake_metadata, fake_vectors)
    assert fake_vectors["upserted"][0]["dims"] == {settings.embedding_dimension}


def test_unsupported_file_marks_failed_with_an_actionable_message(fake_metadata, fake_vectors, fake_embedding):
    document_id, result, row = ingest(None, fake_metadata, fake_vectors, filename="virus.exe", data=b"MZ\x00")

    assert result.indexed is False
    assert row["status"] == DocumentStatus.failed.value
    assert "not supported" in row["error"]


def test_internal_crash_never_leaks_internals_to_the_api(fake_metadata, fake_vectors, fake_embedding, monkeypatch):
    """A Pinecone outage must produce a generic message, not a stack-trace fragment.

    The stored error is shown to the user by the UI, so this is the boundary where an exception message
    becomes public output.
    """
    from app.services import vector_store

    def explode(**kwargs):
        raise RuntimeError("secret internal connection string leaked in this message")

    monkeypatch.setattr(vector_store, "upsert_chunks", explode)

    document_id, result, row = ingest(None, fake_metadata, fake_vectors)

    assert row["status"] == DocumentStatus.failed.value
    assert "secret internal" not in row["error"]
    assert "RuntimeError" in row["error"] or "re-uploading" in row["error"].lower()


def test_scanned_document_fails_cleanly(fake_metadata, fake_vectors, fake_embedding):
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)

    document_id, result, row = ingest(None, fake_metadata, fake_vectors, data=buffer.getvalue())

    assert result.indexed is False
    assert fake_vectors["upserted"] == [], "an empty document must not be indexed"
    assert row["status"] == DocumentStatus.failed.value


def test_delete_removes_vectors_and_metadata_together(fake_metadata, fake_vectors, fake_embedding):
    """Two stores means a delete is a saga.

    Leaving vectors behind would keep answering questions about a document the UI says is gone — a state
    the user cannot repair from the interface.
    """
    document_id, _, _ = ingest(None, fake_metadata, fake_vectors)
    fake_vectors["upserted"].clear()

    removed = ingestion.delete_document_completely(document_id=document_id, owner_id="owner-a")

    assert removed is True
    assert fake_vectors["deleted"] == [{"document_id": document_id, "owner_id": "owner-a"}]
    assert document_id not in fake_metadata.documents


def test_delete_of_other_owner_document_is_refused(fake_metadata, fake_vectors, fake_embedding):
    document_id, _, _ = ingest(None, fake_metadata, fake_vectors)

    assert ingestion.delete_document_completely(document_id=document_id, owner_id="owner-b") is False
    assert fake_vectors["deleted"] == [], "cross-owner delete must never reach the vector store"
    assert document_id in fake_metadata.documents


def test_document_id_is_unguessable(fake_metadata):
    """Ids appear in citations and URLs, so a sequential handle would leak volume and order."""
    ids = {ingestion.new_document_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(candidate) == 32 for candidate in ids)
