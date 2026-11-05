"""
services/vector_store.py — Pinecone reads and writes for chunk vectors.

One index holds every chunk from every document; documents are separated by metadata rather than by
physical indexes. That is what lets a single query search across documents ("compare these two
contracts") without fanning out to N indexes.

The two rules enforced here:

  1. Every query filters on owner_id. Cross-user leakage is a correctness bug, not just a privacy
     one, so the filter is applied inside the service rather than trusted to callers to remember.
  2. Re-indexing deletes first. Upserting with the same chunk id overwrites but still consumes a
     write unit, and the Starter tier caps at 2M/month — re-uploading a 300-page PDF a dozen times
     while debugging would quietly eat the month.

Sync pymongo/pinecone clients are used deliberately. FastAPI runs synchronous dependency and
endpoint functions in a worker threadpool, so a blocking network call here does not stall the event
loop, and it keeps the connection-pool story simple on a free tier where concurrency is ~10 ops/sec
anyway.
"""

from __future__ import annotations

import logging
from typing import Any

from pinecone import Pinecone, ServerlessSpec

from app.core.config import settings
from app.models.document import RetrievedChunk

logger = logging.getLogger(__name__)

# Pinecone accepts large batches, but smaller ones give resumable progress on a big upload and keep
# each request well inside the per-request payload limit.
_UPSERT_BATCH_SIZE = 100

_pc: Pinecone | None = None
_index_cache: dict[str, Any] = {}


def _client() -> Pinecone:
    global _pc
    if _pc is None:
        _pc = Pinecone(api_key=settings.pinecone_api_key)
    return _pc


def get_index():
    """Return the app's index, creating it on first use.

    Caching the index handle avoids a describe round-trip on every request. Creating the index from
    application code is intentional for a solo free-tier project: a dimension or model change then
    fails loudly and immediately instead of surfacing as a confusing similarity score months later.
    """
    name = settings.pinecone_index_name
    if name in _index_cache:
        return _index_cache[name]

    client = _client()
    if name not in client.list_indexes().names():
        logger.info("creating pinecone index %s (%d dims)", name, settings.embedding_dimension)
        client.create_index(
            name=name,
            dimension=settings.embedding_dimension,
            # Cosine: we normalise embeddings, so cosine and dot product agree, and cosine keeps
            # scores comparable between short and long chunks.
            metric="cosine",
            spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region),
        )
        # Creation is asynchronous on Pinecone's side; upserting too early returns 409.
        client.wait_until_ready(name)

    index = client.Index(name)
    _index_cache[name] = index
    return index


def vector_id(document_id: str, chunk_index: int) -> str:
    """Deterministic vector id: {document_id}_{chunk_index:04d}.

    Zero-padding keeps ids sortable and, crucially, makes re-uploading the same document address the
    same vectors instead of creating duplicates.
    """
    return f"{document_id}_{chunk_index:04d}"


def upsert_chunks(
    *,
    document_id: str,
    owner_id: str,
    filename: str,
    vectors: list[tuple[str, list[float]]],
    metadatas: list[dict[str, Any]],
) -> int:
    """Write embedded chunks, deleting any previous vectors for the document first.

    chunk_text is stored in Pinecone metadata rather than in Mongo: Atlas M0 caps at 512 MB, while
    Pinecone needs the text attached anyway to build a citation without a second lookup.
    """
    if not vectors:
        return 0

    delete_document_vectors(document_id=document_id, owner_id=owner_id)

    index = get_index()
    written = 0
    for start in range(0, len(vectors), _UPSERT_BATCH_SIZE):
        batch = vectors[start : start + _UPSERT_BATCH_SIZE]
        batch_meta = metadatas[start : start + _UPSERT_BATCH_SIZE]
        index.upsert(
            vectors=[
                {
                    "id": chunk_id,
                    "values": values,
                    # Pinecone metadata must be flat str/int/float/bool; lists and None are rejected.
                    "metadata": {
                        key: value
                        for key, value in {**batch_meta[offset], "owner_id": owner_id}.items()
                        if isinstance(value, (str, int, float, bool))
                    },
                }
                for offset, (chunk_id, values) in enumerate(batch)
            ]
        )
        written += len(batch)
        logger.debug("upserted %d/%d chunks for %s", written, len(vectors), document_id)

    return written


def query_similar(
    *,
    owner_id: str,
    query_vector: list[float],
    top_k: int = 6,
    document_ids: list[str] | None = None,
    score_threshold: float | None = None,
) -> list[RetrievedChunk]:
    """Nearest-neighbour search scoped to one owner, optionally to specific documents.

    The owner filter is always applied. Without it, any user's question could surface another user's
    document text — the filter is what makes the multi-tenant design safe from day one.
    """
    index = get_index()
    filters: dict[str, Any] = {"owner_id": owner_id}
    if document_ids:
        # $in narrows to a chosen subset, which is what "answer from these two files" needs.
        filters["document_id"] = {"$in": list(document_ids)}

    response = index.query(
        vector=query_vector,
        top_k=top_k,
        include_metadata=True,
        # `filter`, singular. The client takes **kwargs, so `filters=` is accepted by Python and then
        # dropped: the query runs unscoped, which here means owner isolation silently disappears. The
        # name is pinned by a test that calls the real client signature, because a fake of this module
        # cannot catch it.
        filter=filters,
    )

    results: list[RetrievedChunk] = []
    for match in response.matches or []:
        metadata = match.metadata or {}
        score = float(match.score or 0.0)
        if score_threshold is not None and score < score_threshold:
            # Below the floor the chunk is not evidence; passing it to the LLM invites a confident
            # hallucination sourced from an irrelevant paragraph.
            continue
        chunk_text = str(metadata.get("chunk_text", ""))
        if not chunk_text:
            logger.warning("match %s has no chunk_text in metadata; skipped", match.id)
            continue
        results.append(
            RetrievedChunk(
                chunk_id=str(match.id),
                document_id=str(metadata.get("document_id", "")),
                filename=str(metadata.get("filename", "unknown")),
                page_number=int(metadata.get("page_number", 0) or 0),
                chunk_text=chunk_text,
                score=score,
            )
        )
    return results


def delete_document_vectors(*, document_id: str, owner_id: str) -> int:
    """Remove every vector for one document, filtered by owner.

    Returns the deleted count from index stats when available; Pinecone's delete-by-filter does not
    report a per-call number, so the count is best-effort and only used for logging.
    """
    index = get_index()
    filters = {"owner_id": owner_id, "document_id": document_id}
    existing = index.query(
        vector=[0.0] * settings.embedding_dimension, top_k=1, filter=filters, include_metadata=False
    )
    if not existing.matches:
        # Nothing indexed yet, so there is nothing to overwrite — saves a pointless delete bill.
        return 0
    index.delete(filter=filters, delete_all=False)
    return 1


def index_statistics() -> dict[str, Any]:
    """Vector count and pod info, surfaced through /api/health in later phases."""
    try:
        stats = get_index().describe_index_stats()
        return {"total_vector_count": stats.total_vector_count, "namespaces": list(stats.namespaces or {})}
    except Exception as exc:  # health checks must degrade, never crash
        logger.warning("pinecone stats unavailable: %s", exc)
        return {"total_vector_count": None, "error": str(exc)}
