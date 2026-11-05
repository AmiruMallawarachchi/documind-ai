"""
services/ingestion.py — The pipeline that turns an uploaded byte blob into searchable vectors.

    bytes -> extract segments -> chunk -> embed -> Pinecone vectors + Mongo metadata

Runs in a FastAPI BackgroundTask, so the upload endpoint returns immediately and the UI polls for
status. Holding an HTTP request open through a multi-minute embedding step would tie up a worker for
the whole job and time out behind Render's proxy.

Status is the contract with the client, and every terminal path writes it. A document left at
'parsing' because an exception escaped would be stuck spinning in the UI forever, so the except
branch is not optional tidy-up — it is what makes failure visible instead of silent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.models.document import DocumentStatus
from app.services import metadata_store, vector_store
from app.services.chunking import Chunk, chunk_segments
from app.services.embedding_service import embed_texts
from app.services.text_extractor import (
    ExtractionError,
    UnsupportedFileType,
    extract_segments,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    """What the pipeline reports back, and what the documents row records."""

    document_id: str
    chunk_count: int
    page_count: int
    indexed: bool
    error: str | None = None


def new_document_id() -> str:
    """UUID4 hex, not a Mongo ObjectId string.

    The id is the Pinecone vector-id prefix and appears in citations, so it is a public-facing
    identifier. A UUID4 is unguessable; a sequential ObjectId reveals upload order and volume, which
    is business information a free demo should not hand out.
    """
    return uuid.uuid4().hex


def build_chunk_records(
    chunks: list[Chunk],
    *,
    document_id: str,
    owner_id: str,
    filename: str,
) -> list[dict[str, object]]:
    """Shape chunks into Pinecone metadata payloads.

    Kept separate from the embedding call so the metadata contract is directly unit-testable — this
    is the exact data every citation is built from, and a wrong key here silently breaks attribution.
    """
    return [
        {
            "chunk_id": vector_store.vector_id(document_id, chunk.chunk_index),
            "document_id": document_id,
            "owner_id": owner_id,
            "filename": filename,
            "chunk_index": chunk.chunk_index,
            "page_number": chunk.page_number,
            # Pinecone stores the text so retrieval can render a citation without a second lookup.
            "chunk_text": chunk.text,
            "char_count": chunk.char_count,
        }
        for chunk in chunks
    ]


def run_ingestion(
    *,
    document_id: str,
    owner_id: str,
    filename: str,
    data: bytes,
) -> IngestionResult:
    """Parse, chunk, embed, and index one document. Never raises.

    Everything an end user can influence — file contents, size, encoding — arrives through here, and
    any of it can fail. The caller already handed back a 202 and a document_id, so the only honest
    way to report trouble is to write status='failed' with a readable message onto the record.
    """
    logger.info("ingest start document_id=%s filename=%s bytes=%d", document_id, filename, len(data))
    metadata_store.mark_status(document_id, owner_id, DocumentStatus.parsing)

    try:
        segments = extract_segments(filename, data)
        chunks = chunk_segments(segments)
        page_count = max((segment.page_number for segment in segments), default=0)

        if not chunks:
            # Reachable with a file of only whitespace, and worth distinguishing from an extraction
            # failure so the UI can say "no indexable text" rather than "something broke".
            metadata_store.mark_status(
                document_id, owner_id, DocumentStatus.failed, error="No indexable text found."
            )
            return IngestionResult(document_id, 0, page_count, False, "No indexable text found.")

        vectors = embed_texts([chunk.text for chunk in chunks])
        records = build_chunk_records(chunks, document_id=document_id, owner_id=owner_id, filename=filename)

        written = vector_store.upsert_chunks(
            document_id=document_id,
            owner_id=owner_id,
            filename=filename,
            vectors=[(str(record["chunk_id"]), vector) for record, vector in zip(records, vectors)],
            metadatas=[dict(record) for record in records],
        )

        metadata_store.update_document(
            document_id,
            owner_id,
            status=DocumentStatus.indexed.value,
            chunk_count=written,
            page_count=page_count,
            error=None,
        )
        logger.info("ingest done document_id=%s chunks=%d pages=%d", document_id, written, page_count)
        return IngestionResult(document_id, written, page_count, True)

    except (ExtractionError, UnsupportedFileType) as exc:
        # Expected, user-fixable (encrypted PDF, scanned image, oversized file, wrong type): tell them
        # what to do. UnsupportedFileType is a sibling of ExtractionError rather than a subclass, so
        # catching only one would route the other into the generic branch below and hide the reason.
        logger.warning("ingest rejected document_id=%s: %s", document_id, exc)
        metadata_store.mark_status(document_id, owner_id, DocumentStatus.failed, error=str(exc))
        return IngestionResult(document_id, 0, 0, False, str(exc))

    except Exception as exc:
        # Unexpected (Pinecone outage, model load failure). Logged with a traceback for the operator,
        # but the user gets a generic line so an internal stack trace never becomes public API output.
        logger.exception("ingest crashed document_id=%s", document_id)
        safe = f"Unexpected error during indexing: {type(exc).__name__}. Try re-uploading."
        metadata_store.mark_status(document_id, owner_id, DocumentStatus.failed, error=safe)
        return IngestionResult(document_id, 0, 0, False, safe)


def delete_document_completely(*, document_id: str, owner_id: str) -> bool:
    """Remove vectors and metadata together.

    Two stores means a delete has to be a saga, not a call. Dropping only the Mongo row would leave
    orphan vectors that keep answering questions about a document the UI says was deleted — a
    confusing state the user cannot fix from the interface.
    """
    record = metadata_store.get_document(document_id, owner_id)
    if record is None:
        return False

    try:
        vector_store.delete_document_vectors(document_id=document_id, owner_id=owner_id)
    except Exception as exc:
        # A failed vector delete must not silently report success; the row stays so the user can retry.
        logger.exception("vector delete failed for %s", document_id)
        metadata_store.mark_status(document_id, owner_id, DocumentStatus.failed, error=f"Delete incomplete: {exc}")
        return False

    metadata_store.delete_document(document_id, owner_id)
    logger.info("document deleted document_id=%s", document_id)
    return True
