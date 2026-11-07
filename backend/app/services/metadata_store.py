"""
services/metadata_store.py — MongoDB Atlas for document, conversation, and message metadata.

Mongo stores the *about* — filename, status, chunk counts, conversation history, citations — and
never the full chunk text, which lives in Pinecone. Atlas M0 is a hard 512 MB cap, so storing body
text here is the fastest way to fill a free cluster while gaining nothing: retrieval already returns
text from Pinecone metadata.

Collections (see system.md data model):
  documents      one record per uploaded file, with an ingestion status
  conversations  a chat thread header
  messages       one turn, with citations and the agent trace that produced it
  users          one account — email, Argon2id hash, verification flag (owned by user_store)

Sync pymongo is used for the same reason as Pinecone: FastAPI runs sync endpoints in a worker
threadpool, so blocking calls do not stall the event loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

from app.core.config import settings
from app.models.document import DocumentResponse, DocumentStatus

logger = logging.getLogger(__name__)

_client: MongoClient | None = None
_db = None

_DOCUMENTS = "documents"
_CONVERSATIONS = "conversations"
_MESSAGES = "messages"
# Declared here because this module owns the cold-start index pass for the whole database, and a
# second client for accounts would double the connection count against Atlas M0's small pool.
_USERS = "users"


def _database():
    """Lazy singleton client.

    Opening a new MongoClient per request leaks a monitor thread per client and would exhaust Atlas
    M0's ~100 ops/sec within seconds under even light load. One process-wide client, reused.
    """
    global _client, _db
    if _db is None:
        _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
        _db = _client[settings.mongo_database_name]
        _ensure_indexes(_db)
    return _db


def _ensure_indexes(db) -> None:
    """Create the indexes every hot path needs, for every collection in this database.

    Idempotent and cheap when already present (the create call becomes a no-op server-side). The
    owner-scoped compound indexes are what keep listing and retrieval fast as the collection grows,
    and they mirror the filters Pinecone applies so both stores answer the same query shape.

    The account indexes are created here too rather than in user_store because this is the one place
    that runs once, against a live client. `email` is unique: it is the only thing standing between two
    simultaneous signups for one address and two rows with two different hashes.
    """
    try:
        db[_DOCUMENTS].create_index([("owner_id", ASCENDING), ("uploaded_at", -1)])
        db[_DOCUMENTS].create_index("document_id", unique=True)
        db[_CONVERSATIONS].create_index([("owner_id", ASCENDING), ("updated_at", -1)])
        # One index serves both "load a thread in order" and "append the next message".
        db[_MESSAGES].create_index([("conversation_id", ASCENDING), ("created_at", ASCENDING)])
        db[_MESSAGES].create_index([("owner_id", ASCENDING), ("conversation_id", ASCENDING)])
        db[_USERS].create_index("email", unique=True)
        db[_USERS].create_index("user_id", unique=True)
    except PyMongoError as exc:
        # Do not crash the app over an index: Atlas may be briefly unreachable during a cold start,
        # and a missing index slows reads rather than corrupting them.
        logger.warning("mongo index creation skipped: %s", exc)


def _utc_now() -> datetime:
    """Timezone-aware UTC.

    WHY: a naive datetime() is stored as an ambiguous local instant; Atlas renders it as though it
    were UTC while JS reads it as local time, so the UI shows a timestamp shifted by the browser's
    offset. Every timestamp in this app is aware UTC and converted only at the display edge.
    """
    return datetime.now(timezone.utc)


def to_iso(moment: datetime | None) -> str:
    """Serialise a Mongo datetime for the API. Public because routers format timestamps too."""
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


_iso = to_iso


def to_response(record: dict[str, Any]) -> DocumentResponse:
    """Project a Mongo document onto the API schema.

    Mongo's _id is an ObjectId and must never reach the client — exposing it invites IDOR-style
    guessing and leaks internal ordering. The public handle is document_id.
    """
    return DocumentResponse(
        document_id=str(record.get("document_id", "")),
        owner_id=str(record.get("owner_id", "")),
        filename=str(record.get("filename", "")),
        content_type=str(record.get("content_type", "")),
        status=record.get("status", DocumentStatus.uploading.value),
        page_count=int(record.get("page_count", 0) or 0),
        chunk_count=int(record.get("chunk_count", 0) or 0),
        size_bytes=int(record.get("size_bytes", 0) or 0),
        uploaded_at=_iso(record.get("uploaded_at")),
        error=record.get("error"),
    )


def create_document_record(
    *,
    document_id: str,
    owner_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Insert the initial 'uploading' record so the UI has something to poll.

    Written before parsing starts, not after: if ingestion crashes midway, the row still exists with
    status='uploading', which is what lets the retry path and the UI show a stuck document instead of
    a vanishing one.
    """
    now = _utc_now()
    record = {
        "document_id": document_id,
        "owner_id": owner_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "status": DocumentStatus.uploading.value,
        "page_count": 0,
        "chunk_count": 0,
        "uploaded_at": now,
        "updated_at": now,
        "error": None,
    }
    _database()[_DOCUMENTS].insert_one(record)
    return record


def update_document(document_id: str, owner_id: str, **fields: Any) -> None:
    """Patch one document, always scoped by owner_id.

    Ownership is part of the filter rather than checked afterwards: an update keyed only on
    document_id would let one caller rewrite another user's row if a document id ever leaked.
    """
    fields["updated_at"] = _utc_now()
    _database()[_DOCUMENTS].update_one(
        {"document_id": document_id, "owner_id": owner_id},
        {"$set": fields},
    )


def mark_status(document_id: str, owner_id: str, status: DocumentStatus, error: str | None = None) -> None:
    """Single place where status transitions happen, so the enum stays the only source of truth."""
    update_document(document_id, owner_id, status=status.value, error=error)


def get_document(document_id: str, owner_id: str) -> dict[str, Any] | None:
    return _database()[_DOCUMENTS].find_one({"document_id": document_id, "owner_id": owner_id})


def list_documents(owner_id: str, limit: int = 100) -> list[dict[str, Any]]:
    cursor = (
        _database()[_DOCUMENTS]
        .find({"owner_id": owner_id})
        .sort("uploaded_at", -1)
        .limit(limit)
    )
    return list(cursor)


def get_indexed_document_ids(owner_id: str) -> list[str]:
    """Only successfully indexed documents — the default corpus for a question.

    Filtering on status here is what stops a half-failed upload from contributing empty chunks to an
    answer.
    """
    cursor = _database()[_DOCUMENTS].find(
        {"owner_id": owner_id, "status": DocumentStatus.indexed.value},
        {"document_id": 1, "_id": 0},
    )
    return [str(row["document_id"]) for row in cursor if row.get("document_id")]


def filenames_for(owner_id: str, document_ids: list[str]) -> dict[str, str]:
    """document_id -> filename, for citations. One small read instead of N lookups."""
    if not document_ids:
        return {}
    cursor = _database()[_DOCUMENTS].find(
        {"owner_id": owner_id, "document_id": {"$in": document_ids}},
        {"document_id": 1, "filename": 1, "_id": 0},
    )
    return {str(row["document_id"]): str(row.get("filename", "unknown")) for row in cursor}


def delete_document(document_id: str, owner_id: str) -> bool:
    result = _database()[_DOCUMENTS].delete_one({"document_id": document_id, "owner_id": owner_id})
    return result.deleted_count > 0


def create_conversation(owner_id: str, title: str = "New chat") -> dict[str, Any]:
    now = _utc_now()
    record = {
        "conversation_id": str(ObjectId()),
        "owner_id": owner_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }
    _database()[_CONVERSATIONS].insert_one(record)
    return record


def get_conversation(owner_id: str, conversation_id: str) -> dict[str, Any] | None:
    """Fetch one thread header, scoped by owner.

    owner_id belongs in the query rather than in a comparison afterwards: a conversation id is a
    Mongo ObjectId, guessable in sequence, so an unscoped lookup would hand one user another user's
    full chat history from a URL parameter alone.
    """
    return _database()[_CONVERSATIONS].find_one(
        {"conversation_id": conversation_id, "owner_id": owner_id}
    )


def list_conversations(owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
    cursor = (
        _database()[_CONVERSATIONS]
        .find({"owner_id": owner_id})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return list(cursor)


def update_conversation(owner_id: str, conversation_id: str, **fields: Any) -> bool:
    """Rename or pin a thread. Returns False when there is no such thread for this owner.

    `updated_at` is deliberately not touched. The history list is ordered by it, so a rename would
    fling a six-month-old thread to the top of the sidebar and a pin would do it twice over — the user
    asked to label the thread, not to resume it. Only `save_message` moves a thread up the list.
    """
    if not fields:
        return False
    result = _database()[_CONVERSATIONS].update_one(
        {"conversation_id": conversation_id, "owner_id": owner_id},
        {"$set": fields},
    )
    return result.matched_count > 0


def delete_conversation(owner_id: str, conversation_id: str) -> bool:
    """Remove a thread and every message in it. Returns False when there is no such thread.

    The messages go first. If the second delete fails, the user is left with a thread that renders
    empty and can be deleted again; if the order were reversed and the first failed, the rows would
    outlive their header as invisible orphans that nothing can list, count or clean up.
    """
    _database()[_MESSAGES].delete_many(
        {"conversation_id": conversation_id, "owner_id": owner_id}
    )
    result = _database()[_CONVERSATIONS].delete_one(
        {"conversation_id": conversation_id, "owner_id": owner_id}
    )
    return result.deleted_count > 0


def save_message(
    *,
    owner_id: str,
    conversation_id: str,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    agent_trace: list[str] | None = None,
) -> dict[str, Any]:
    """Persist one chat turn with its citations and the agent path that produced it.

    agent_trace is stored, not discarded. When an answer is wrong, "which agents ran and in what
    order" is the difference between debugging and guessing, and it is exactly the evidence a viva
    question about the multi-agent design needs.
    """
    now = _utc_now()
    record = {
        "conversation_id": conversation_id,
        "owner_id": owner_id,
        "role": role,
        "content": content,
        "citations": citations or [],
        "agent_trace": agent_trace or [],
        "created_at": now,
    }
    _database()[_MESSAGES].insert_one(record)
    _database()[_CONVERSATIONS].update_one(
        {"conversation_id": conversation_id, "owner_id": owner_id},
        {"$set": {"updated_at": now}},
    )
    return record


def get_recent_messages(owner_id: str, conversation_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """Latest turns in chronological order, for the LLM's conversation window.

    Sorted descending then reversed so Mongo uses the (conversation_id, created_at) index and reads
    only the last `limit` rows. Loading an entire multi-year history into every prompt would be both
    slow and a silent token-budget leak.
    """
    cursor = (
        _database()[_MESSAGES]
        .find({"owner_id": owner_id, "conversation_id": conversation_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    return list(reversed(list(cursor)))


def get_conversation_messages(owner_id: str, conversation_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Full thread in chronological order for rendering the UI."""
    cursor = (
        _database()[_MESSAGES]
        .find({"owner_id": owner_id, "conversation_id": conversation_id})
        .sort("created_at", 1)
        .limit(limit)
    )
    return list(cursor)


def ping() -> bool:
    """Connectivity probe for /api/health, so a dead Atlas shows up before a user hits it.

    The serverSelectionTimeoutMS on the client bounds how long this can block; without it a
    unreachable cluster would hang the health endpoint itself.
    """
    try:
        _database().command("ping")
        return True
    except PyMongoError as exc:
        logger.warning("mongo ping failed: %s", exc)
        return False
