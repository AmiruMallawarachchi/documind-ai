"""
tests/conftest.py — Shared fixtures: fake stores and a TestClient wired to them.

WHY fake the service boundaries instead of using real Pinecone/Mongo/Groq:
  1. Credentials. CI and a fresh clone have no keys, and a suite that cannot run without paid services
     is a suite that quietly stops being run.
  2. Speed and determinism. A real embedding plus a vector query per assertion turns a milliseconds-long
     suite into minutes, and similarity scores drift between model versions.
  3. Failure injection. "What happens when Groq returns 429" is only testable if the test controls that
     boundary; against a live service, whether it happens is up to the provider.

HOW the patching works, which matters for maintenance: code that calls `metadata_store.foo(...)`
resolves the attribute at call time, so patching the module attribute covers every caller at once.
Code that did `from x import foo` holds its own reference, so those names are patched where they are
used (app.services.retrieval.embed_query, and complete_text in each agent module). If a refactor changes
an import style, patch the new location — a stale patch silently tests real network code, which is the
failure mode to watch for in this file.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

# Must run before anything imports app.core.config: pydantic-settings validates required fields at
# import time, so the environment has to be populated when the app module first loads.
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("PINECONE_API_KEY", "test-pinecone-key")
os.environ.setdefault("PINECONE_INDEX_NAME", "test-index")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-a-real-secret")
os.environ.setdefault("DEMO_ACCESS_CODE", "test-access-code")
# Generous by default so the suite never trips a limit by accident; the rate-limit tests lower these on the
# settings object, which the dependencies read at call time.
os.environ.setdefault("CHAT_REQUESTS_PER_MINUTE", "1000")
os.environ.setdefault("UPLOAD_REQUESTS_PER_MINUTE", "1000")
os.environ.setdefault("LOGIN_REQUESTS_PER_MINUTE", "1000")
# Tests run under the same no-wildcard CORS rule as production, so a regression there shows up here.
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
# Left at the default (development) so the X-Demo-Key fallback stays available: the isolation tests stand
# up two namespaces with that header, and tests/test_auth.py flips the environment to production to prove
# the JWT path is what a deployed instance accepts.

import pytest
from fastapi.testclient import TestClient

from app.models.document import RetrievedChunk

# Mongo returns real datetime objects, so the fake must too. An ISO string here made to_iso receive the
# wrong type and the failure looked like a bug in the service layer rather than an unfaithful fake.
STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeMetadataStore:
    """Dict-backed store with the same call signatures as metadata_store.

    Owner scoping is implemented rather than skipped: a fake that leaks across owners would let the
    isolation tests pass against something more permissive than production.
    """

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.conversations: dict[str, dict] = {}
        self.messages: list[dict] = []
        self._counter = 0

    def create_document_record(self, **kwargs):
        record = {
            "status": "uploading",
            "page_count": 0,
            "chunk_count": 0,
            "error": None,
            "uploaded_at": STAMP,
            "updated_at": STAMP,
            **kwargs,
        }
        self.documents[record["document_id"]] = record
        return record

    def _owned(self, document_id, owner_id):
        record = self.documents.get(document_id)
        return record if record and record["owner_id"] == owner_id else None

    def mark_status(self, document_id, owner_id, status, error=None):
        record = self._owned(document_id, owner_id)
        if record:
            record["status"] = getattr(status, "value", status)
            record["error"] = error

    def update_document(self, document_id, owner_id, **fields):
        record = self._owned(document_id, owner_id)
        if record:
            record.update(fields)

    def get_document(self, document_id, owner_id):
        return self._owned(document_id, owner_id)

    def list_documents(self, owner_id, limit=100):
        rows = [doc for doc in self.documents.values() if doc["owner_id"] == owner_id]
        return list(reversed(rows))[:limit]

    def get_indexed_document_ids(self, owner_id):
        return [
            doc["document_id"]
            for doc in self.documents.values()
            if doc["owner_id"] == owner_id and doc["status"] == "indexed"
        ]

    def filenames_for(self, owner_id, document_ids):
        return {
            doc["document_id"]: doc["filename"]
            for doc in self.documents.values()
            if doc["owner_id"] == owner_id and doc["document_id"] in document_ids
        }

    def delete_document(self, document_id, owner_id):
        if self._owned(document_id, owner_id) is None:
            return False
        del self.documents[document_id]
        return True

    def create_conversation(self, owner_id, title="New chat"):
        self._counter += 1
        conversation_id = f"conv-{self._counter}"
        record = {
            "conversation_id": conversation_id,
            "owner_id": owner_id,
            "title": title,
            "created_at": STAMP,
            "updated_at": STAMP,
        }
        self.conversations[conversation_id] = record
        return record

    def get_conversation(self, owner_id, conversation_id):
        record = self.conversations.get(conversation_id)
        return record if record and record["owner_id"] == owner_id else None

    def list_conversations(self, owner_id, limit=50):
        return [row for row in self.conversations.values() if row["owner_id"] == owner_id][:limit]

    def save_message(self, **kwargs):
        self.messages.append(dict(kwargs))
        return kwargs

    def _thread(self, owner_id, conversation_id):
        return [
            m
            for m in self.messages
            if m["owner_id"] == owner_id and m["conversation_id"] == conversation_id
        ]

    def get_recent_messages(self, owner_id, conversation_id, limit=8):
        return self._thread(owner_id, conversation_id)[-limit:]

    def get_conversation_messages(self, owner_id, conversation_id, limit=200):
        return self._thread(owner_id, conversation_id)[:limit]

    def ping(self):
        return True


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Empty the limiter between tests.

    Its windows are process-global and keyed by owner id, and the suite reuses "owner-a" everywhere, so a
    fixed number of requests per minute would fail tests in a order-dependent way — the worst kind of red.
    """
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def fake_metadata(monkeypatch) -> FakeMetadataStore:
    from app.services import metadata_store

    fake = FakeMetadataStore()
    for name in [
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
    ]:
        monkeypatch.setattr(metadata_store, name, getattr(fake, name))
    # to_response and to_iso stay real: they are pure mappings, and testing them is the point.
    return fake


@pytest.fixture
def fake_vectors(monkeypatch) -> dict:
    """Capture what ingestion would write to Pinecone; serve canned query results.

    Asserting on the captured payload is how the chunk-id scheme, the owner_id field, and the
    "chunk_text lives in metadata" rule get verified without a network round-trip.
    """
    from app.services import retrieval, vector_store

    captured: dict = {"upserted": [], "queries": [], "deleted": [], "canned_results": [], "raise_query": None}

    def fake_upsert(*, document_id, owner_id, filename, vectors, metadatas):
        captured["upserted"].append(
            {
                "document_id": document_id,
                "owner_id": owner_id,
                "filename": filename,
                "count": len(vectors),
                "ids": [chunk_id for chunk_id, _ in vectors],
                "metadatas": metadatas,
                "dims": {len(values) for _, values in vectors},
            }
        )
        return len(vectors)

    def fake_query(*, owner_id, query_vector, top_k=6, document_ids=None, score_threshold=None):
        captured["queries"].append(
            {
                "owner_id": owner_id,
                "top_k": top_k,
                "document_ids": document_ids,
                "score_threshold": score_threshold,
                "vector_length": len(query_vector),
            }
        )
        # An error raised here is a search that died mid-stream, which no agent can absorb and is the only
        # way to test the in-band error frame.
        if captured["raise_query"] is not None:
            raise captured["raise_query"]
        return list(captured["canned_results"])[:top_k]

    def fake_delete(*, document_id, owner_id):
        captured["deleted"].append({"document_id": document_id, "owner_id": owner_id})
        return 1

    monkeypatch.setattr(vector_store, "upsert_chunks", fake_upsert)
    monkeypatch.setattr(vector_store, "delete_document_vectors", fake_delete)
    monkeypatch.setattr(retrieval, "query_similar", fake_query)
    return captured


@pytest.fixture
def fake_embedding(monkeypatch):
    """Deterministic vectors, so ranking assertions test logic rather than model behaviour."""
    from app.services import embedding_service, ingestion, retrieval

    def embed_texts(texts):
        return [[float(len(text))] + [0.0] * 383 for text in texts]

    def embed_query(text):
        return [float(len(text))] + [0.0] * 383

    monkeypatch.setattr(embedding_service, "embed_texts", embed_texts)
    monkeypatch.setattr(embedding_service, "embed_query", embed_query)
    # Both modules imported the name directly, so patch the bound reference too.
    monkeypatch.setattr(ingestion, "embed_texts", embed_texts)
    monkeypatch.setattr(retrieval, "embed_query", embed_query)
    return embed_texts


@pytest.fixture
def fake_llm(monkeypatch) -> dict:
    """Script the agents' text completions without touching Groq.

    Replies are chosen by which system prompt is being answered rather than by call order, so a test that
    re-orders the graph still gets a coherent conversation back. `calls` records every completion and
    `kinds` records which agent asked, which is how the assertions distinguish "the critic never ran" from
    "nothing ran at all".
    """
    from app.agents import critique_agent, router_agent, synthesis_agent
    from app.services import prompts

    state = {
        "answer": "The fee is 250 dollars",
        "route": "LABEL: SEARCH\nREASON: asks about a document term",
        "verdict": "VERDICT: PASS\nISSUES: NONE",
        "raises": None,
        "calls": [],
        "kinds": [],
    }

    def fake_complete(messages, **kwargs):
        state["calls"].append(messages)
        system = str(messages[0].content) if messages else ""
        # Identified by the system prompt each agent owns, not by call order: the graph reaches synthesis
        # twice on a revision pass, and an order-based fake would mislabel the retry as a critique.
        if system == prompts.ROUTER_SYSTEM_PROMPT:
            kind, reply = "router", state["route"]
        elif system == prompts.CRITIQUE_SYSTEM_PROMPT:
            kind, reply = "critique", state["verdict"]
        elif system == prompts.SYSTEM_PROMPT:
            kind, reply = "synthesis", state["answer"]
        else:
            # The chit-chat reply is the only remaining prompt, and it carries its own short system text.
            kind, reply = "direct", state["answer"]
        state["kinds"].append(kind)
        if state["raises"] is not None:
            raise state["raises"]
        return reply

    # Each agent imported complete_text by name, so the patch goes where the reference is held.
    for module in (router_agent, synthesis_agent, critique_agent):
        monkeypatch.setattr(module, "complete_text", fake_complete)
    return state


@pytest.fixture
def client(monkeypatch, fake_metadata, fake_vectors, fake_embedding, fake_llm):
    """TestClient with every external boundary faked and no model download at startup."""
    from app.main import app
    from app.services import embedding_service

    monkeypatch.setattr(embedding_service, "get_model", lambda: object())
    with TestClient(app) as test_client:
        yield test_client


def make_chunk(document_id="doc-1", filename="loan.pdf", page=3, text=None, score=0.62) -> RetrievedChunk:
    """Build a RetrievedChunk for query assertions without repeating the long constructor."""
    return RetrievedChunk(
        chunk_id=f"{document_id}_0000",
        document_id=document_id,
        filename=filename,
        page_number=page,
        chunk_text=text or "The borrower shall repay within 36 monthly installments.",
        score=score,
    )
