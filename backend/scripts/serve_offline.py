"""
scripts/serve_offline.py — Run the real API with every external boundary faked, for UI work.

    ./venv python scripts/serve_offline.py

DocuMind's three dependencies (Pinecone, MongoDB Atlas, Groq) all need credentials, and sentence-
transformers needs a model download. That is fine in production and a blocker on a train: this script
swaps those four boundaries for in-memory stand-ins and starts uvicorn on port 8000, so the frontend can
be driven end to end — upload, chunking, indexing, retrieval, the agent graph, SSE streaming — with no
keys and no network.

What stays real: every router, the chunking and extraction code, the whole agent graph, and the SSE wire
format. What is replaced: storage and the model calls. Retrieval here scores by word overlap rather than
by embedding similarity, so it is a faithful *shape* of search, not a faithful ranking — do not use it to
tune quality, only to exercise the product.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# Must precede any app import: pydantic-settings validates required fields when the module loads.
os.environ.setdefault("GROQ_API_KEY", "offline-placeholder")
os.environ.setdefault("PINECONE_API_KEY", "offline-placeholder")
os.environ.setdefault("PINECONE_INDEX_NAME", "offline-index")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
# Both spellings of the dev origin: `next dev` serves localhost:3000 but a browser opened at
# 127.0.0.1:3000 sends that as its Origin, and a custom header (X-Demo-Key) makes the request
# preflighted — an exact-match allowlist that omits it fails as an opaque "Failed to fetch".
os.environ.setdefault(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)
os.environ.setdefault("JWT_SECRET", "offline-secret-not-used-anywhere-else-32bytes")
# The offline server runs in production mode (see below), so it enforces the same session-token rule a
# deployed instance does. Typing this code in the UI once is what exercises that path locally.
os.environ.setdefault("DEMO_ACCESS_CODE", "offline-demo")
# Skips the MongoDB startup probe in the app lifespan.
os.environ.setdefault("ENVIRONMENT", "production")

from app.agents import critique_agent, router_agent, synthesis_agent  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.models.document import RetrievedChunk  # noqa: E402
from app.services import (  # noqa: E402
    embedding_service,
    ingestion,
    metadata_store,
    prompts,
    retrieval,
    vector_store,
)

# Store: document_id -> {chunk_id: {"text": ..., "metadata": ...}}
_VECTORS: dict[str, dict[str, dict]] = {}

# OFFLINE_LATENCY_MS makes each fake model call sleep, so the per-agent status line and the Stop button
# are visible in the browser. Without it the canned graph finishes in milliseconds and those paths cannot
# be exercised at all.
_LATENCY_S = float(os.environ.get("OFFLINE_LATENCY_MS", "0")) / 1000.0


def _sleep() -> None:
    if _LATENCY_S:
        time.sleep(_LATENCY_S)

METADATA_NAMES = [
    "create_document_record",
    "mark_status",
    "update_document",
    "get_document",
    "list_documents",
    "get_indexed_document_ids",
    "filenames_for",
    "delete_document",
    "create_conversation",
    "get_conversation",
    "list_conversations",
    "save_message",
    "get_recent_messages",
    "get_conversation_messages",
    "ping",
]


def _fake_upsert(*, document_id: str, owner_id: str, filename: str, vectors, metadatas) -> int:
    stored = _VECTORS.setdefault(document_id, {})
    stored.clear()
    for (chunk_id, values), metadata in zip(vectors, metadatas, strict=True):
        stored[chunk_id] = {"text": metadata.get("chunk_text", ""), "metadata": metadata, "vector": values}
    return len(stored)


def _fake_delete(*, document_id: str, owner_id: str) -> int:
    return len(_VECTORS.pop(document_id, {}))


def _fake_query(*, owner_id, query_vector, top_k=6, document_ids=None, score_threshold=None):
    """Score by word overlap. Deterministic, credential-free, and good enough to produce real citations.

    Returns RetrievedChunk, exactly like vector_store.query_similar: retrieval.py's dedupe reads
    `chunk.chunk_text`, so a dict here would crash the pipeline rather than the fake.
    """
    terms = set(re.findall(r"[a-zA-Z0-9']{4,}", str(query_vector).lower()))
    matches: list[RetrievedChunk] = []
    for document_id, chunks in _VECTORS.items():
        if document_ids and document_id not in document_ids:
            continue
        for chunk_id, chunk in chunks.items():
            metadata = chunk["metadata"]
            if metadata.get("owner_id") != owner_id:
                continue
            words = set(re.findall(r"[a-zA-Z0-9']{4,}", chunk["text"].lower()))
            overlap = len(terms & words) / max(len(terms), 1)
            score = round(0.2 + overlap * 0.7, 4)
            if overlap <= 0 or (score_threshold is not None and score < score_threshold):
                continue
            matches.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    filename=str(metadata.get("filename", document_id)),
                    page_number=int(metadata.get("page_number", 1)),
                    chunk_text=chunk["text"],
                    score=score,
                )
            )
    matches.sort(key=lambda chunk: chunk.score, reverse=True)
    return matches[:top_k]


def _fake_embed_texts(texts):
    # The real vector is 384 floats; only its length is asserted by callers, so echo the text back in the
    # first slot and let _fake_query do the matching on metadata instead.
    return [[text] + [0.0] * (settings.embedding_dimension - 1) for text in texts]


def _fake_embed_query(text):
    return [text] + [0.0] * (settings.embedding_dimension - 1)


def _fake_complete(messages, **kwargs):
    """Stand in for Groq: route, answer from the first passage, and always pass the draft."""
    _sleep()
    system = str(messages[0].content)
    human = str(messages[-1].content)

    if system == prompts.ROUTER_SYSTEM_PROMPT:
        return "LABEL: SEARCH\nREASON: offline server always searches"
    if system == prompts.CRITIQUE_SYSTEM_PROMPT:
        return "VERDICT: PASS\nISSUES: NONE"

    # Each passage in the context block is followed by a blank line, so stop there: a greedy or
    # end-of-string match would swallow the prompt's own CONVERSATION/QUESTION sections into the answer.
    passage = re.search(r"\[1\] \(source:[^\n]*\)\n(.{0,240}?)(?:\n\n|$)", human, re.DOTALL)
    if not passage:
        return prompts.NO_CONTEXT_ANSWER
    snippet = " ".join(passage.group(1).split())[:220]
    return (
        f"Offline mode: here is what the first retrieved passage says — {snippet} [1]. "
        "Install real credentials for a genuine answer."
    )


def install() -> list[mock._patch]:
    """Apply every patch and keep them alive for the process lifetime."""
    from tests.conftest import FakeMetadataStore

    store = FakeMetadataStore()
    patches = [mock.patch.object(metadata_store, name, getattr(store, name)) for name in METADATA_NAMES]
    patches += [
        mock.patch.object(vector_store, "upsert_chunks", _fake_upsert),
        mock.patch.object(vector_store, "delete_document_vectors", _fake_delete),
        mock.patch.object(vector_store, "get_index", lambda: None),
        mock.patch.object(vector_store, "index_statistics", lambda: {"count": 0, "index_name": "offline"}),
        mock.patch.object(retrieval, "query_similar", _fake_query),
        mock.patch.object(embedding_service, "get_model", lambda: object()),
        mock.patch.object(embedding_service, "embed_texts", _fake_embed_texts),
        mock.patch.object(embedding_service, "embed_query", _fake_embed_query),
        # Both services did `from app.services.embedding_service import embed_texts`, which binds the
        # function into the importing module — patching the defining module alone leaves the real call.
        mock.patch.object(ingestion, "embed_texts", _fake_embed_texts),
        mock.patch.object(retrieval, "embed_query", lambda text: text),
        mock.patch.object(router_agent, "complete_text", _fake_complete),
        mock.patch.object(synthesis_agent, "complete_text", _fake_complete),
        mock.patch.object(critique_agent, "complete_text", _fake_complete),
    ]
    for patcher in patches:
        patcher.start()
    return patches


def main() -> None:
    import uvicorn

    from app.main import app

    install()
    print(
        "\nDocuMind running OFFLINE: no Pinecone, no Mongo, no Groq.\n"
        "Answers are canned by scripts/serve_offline.py — retrieval here is word overlap, not embeddings.\n",
        flush=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
