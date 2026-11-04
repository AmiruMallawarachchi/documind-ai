"""
tests/test_metadata_store.py — The Mongo service, driven by a fake database that records the queries.

Atlas is not reachable from the test suite and should not be: what is worth asserting is the *shape* of
every query this service makes, because its safety rests on that shape. Two rules recur:

  1. owner_id belongs in the filter, never in a comparison afterwards. A conversation id is a Mongo
     ObjectId — sequential and guessable — so an unscoped lookup would hand over another user's chat
     history from a single URL parameter.
  2. the API never sees Mongo's own _id.

Both are invisible to the HTTP tests, which swap in an in-memory fake store. Here they are checked
against the real service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pymongo.errors import OperationFailure

from app.services import metadata_store


class _Result(SimpleNamespace):
    """Stands in for pymongo's InsertOne/UpdateOne/DeleteOne result."""


class FakeCursor:
    """Mimics the chainable pymongo cursor: find(...).sort(...).limit(...) then iterate."""

    def __init__(self, rows, log, collection):
        self._rows = list(rows)
        self._log = log
        self._collection = collection

    def sort(self, key, direction):
        self._log.append((f"{self._collection}.sort", key, direction))
        self._rows.sort(key=lambda row: row.get(key), reverse=(direction == -1))
        return self

    def limit(self, count):
        self._log.append((f"{self._collection}.limit", count))
        self._rows = self._rows[:count]
        return self

    def __iter__(self):
        return iter(self._rows)


def _matches(row, flt):
    """A deliberately small piece of Mongo: equality plus $in. Enough that this fake cannot return a
    row the real database would have filtered out, which is the whole point of testing against it."""
    for key, condition in flt.items():
        value = row.get(key)
        if isinstance(condition, dict):
            if "$in" in condition and value not in condition["$in"]:
                return False
        elif value != condition:
            return False
    return True


class FakeCollection:
    def __init__(self, name, db):
        self._name = name
        self._db = db

    def insert_one(self, record):
        self._db.log.append((f"{self._name}.insert_one", dict(record)))
        self._db.rows.setdefault(self._name, []).append(record)
        return _Result(inserted_id="oid")

    def update_one(self, flt, update):
        self._db.log.append((f"{self._name}.update_one", flt, update))
        return _Result(modified_count=1)

    def delete_one(self, flt):
        self._db.log.append((f"{self._name}.delete_one", flt))
        return _Result(deleted_count=self._db.deleted)

    def find(self, flt, projection=None):
        self._db.log.append((f"{self._name}.find", flt))
        self._db.projections[self._name] = projection
        return FakeCursor(
            [row for row in self._db.rows.get(self._name, []) if _matches(row, flt)],
            self._db.log,
            self._name,
        )

    def find_one(self, flt):
        self._db.log.append((f"{self._name}.find_one", flt))
        for row in self._db.rows.get(self._name, []):
            if _matches(row, flt):
                return row
        return None

    def create_index(self, *args, **kwargs):
        self._db.log.append((f"{self._name}.create_index", args))


class FakeDB:
    """A set of collections plus one running log of everything the service asked for."""

    def __init__(self):
        self.log: list[tuple] = []
        self.rows: dict[str, list[dict]] = {"documents": [], "conversations": [], "messages": []}
        self.projections: dict[str, dict | None] = {}
        self.deleted = 1

    def __getitem__(self, name):
        return FakeCollection(name, self)

    def command(self, name):
        self.log.append(("command", name))
        return {"ok": 1}


@pytest.fixture
def db(monkeypatch) -> FakeDB:
    store = FakeDB()
    monkeypatch.setattr(metadata_store, "_database", lambda: store)
    return store


def logged(store: FakeDB, operation: str) -> list[tuple]:
    return [entry for entry in store.log if entry[0] == operation]


# ── timestamp and response shaping ─────────────────────────────────────────────


def test_a_naive_timestamp_is_read_as_utc_not_as_local_time():
    """Atlas shows a naive datetime as UTC while the browser reads it as local, so the history would
    render shifted by the machine's offset. Normalising here is what keeps the two agree."""
    assert metadata_store.to_iso(datetime(2026, 11, 4, 12, 0, 0)) == "2026-11-04T12:00:00+00:00"


def test_an_aware_timestamp_is_converted_rather_than_relabelled():
    ist = datetime(2026, 11, 4, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    assert metadata_store.to_iso(ist) == "2026-11-04T12:00:00+00:00"


def test_a_missing_timestamp_serialises_to_an_empty_string():
    assert metadata_store.to_iso(None) == ""


def test_a_record_projects_onto_the_api_schema_without_its_object_id():
    """_id is internal ordering and an IDOR invitation; only document_id is handed out."""
    response = metadata_store.to_response(
        {
            "_id": "507f1f77bcf86cd799439011",
            "document_id": "doc-1",
            "owner_id": "owner-a",
            "filename": "loan.pdf",
            "content_type": "application/pdf",
            "status": "indexed",
            "page_count": 3,
            "chunk_count": 12,
            "size_bytes": 2048,
            "uploaded_at": datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc),
            "error": None,
        }
    )

    assert response.document_id == "doc-1"
    assert response.uploaded_at == "2026-11-04T09:00:00+00:00"
    assert "507f1f77bcf86cd799439011" not in response.model_dump_json()


def test_a_half_written_record_still_projects_with_defaults():
    """An ingestion crash leaves a partial row; listing it must not 500 the documents endpoint."""
    response = metadata_store.to_response({"document_id": "doc-1", "status": "parsing"})

    assert response.page_count == 0
    assert response.chunk_count == 0
    assert response.uploaded_at == ""
    assert response.error is None


# ── owner scoping ──────────────────────────────────────────────────────────────


def test_a_document_lookup_is_keyed_on_owner_and_id_together(db):
    db.rows["documents"] = [{"document_id": "doc-1", "owner_id": "owner-a", "filename": "loan.pdf"}]

    assert metadata_store.get_document("doc-1", "owner-a") is not None
    assert metadata_store.get_document("doc-1", "owner-b") is None
    assert [entry[1] for entry in logged(db, "documents.find_one")] == [
        {"document_id": "doc-1", "owner_id": "owner-a"},
        {"document_id": "doc-1", "owner_id": "owner-b"},
    ]


def test_a_document_update_cannot_touch_another_owners_row(db):
    metadata_store.update_document("doc-1", "owner-a", status="indexed")

    flt, update = logged(db, "documents.update_one")[-1][1:]
    assert flt == {"document_id": "doc-1", "owner_id": "owner-a"}
    assert update["$set"]["status"] == "indexed"
    assert "updated_at" in update["$set"]


def test_a_conversation_load_is_scoped_by_owner(db):
    """The URL carries only the conversation id, so the owner filter is the whole defence."""
    metadata_store.get_conversation("owner-a", "conv-1")

    assert logged(db, "conversations.find_one")[-1][1] == {"conversation_id": "conv-1", "owner_id": "owner-a"}


def test_a_status_transition_goes_through_the_owner_scoped_update(db):
    metadata_store.mark_status("doc-1", "owner-a", metadata_store.DocumentStatus.failed, error="no text layer")

    flt, update = logged(db, "documents.update_one")[-1][1:]
    assert flt == {"document_id": "doc-1", "owner_id": "owner-a"}
    assert update["$set"]["status"] == "failed"
    assert update["$set"]["error"] == "no text layer"


def test_listing_documents_is_scoped_and_newest_first(db):
    db.rows["documents"] = [
        {"document_id": "old", "owner_id": "owner-a", "uploaded_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"document_id": "new", "owner_id": "owner-a", "uploaded_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ]

    results = metadata_store.list_documents("owner-a", limit=5)

    assert logged(db, "documents.find")[-1][1] == {"owner_id": "owner-a"}
    assert ("documents.sort", "uploaded_at", -1) in db.log
    assert ("documents.limit", 5) in db.log
    assert [row["document_id"] for row in results] == ["new", "old"]


def test_the_default_corpus_excludes_a_document_that_failed_to_index(db):
    """A half-failed upload contributing empty chunks to an answer is the failure this filter prevents."""
    db.rows["documents"] = [
        {"document_id": "doc-1", "owner_id": "owner-a", "status": "indexed"},
        {"document_id": "doc-2", "owner_id": "owner-a", "status": "failed"},
        {"document_id": "doc-3", "owner_id": "owner-a", "status": "indexed"},
        # Another user's healthy document must never join this one's corpus.
        {"document_id": "doc-4", "owner_id": "owner-b", "status": "indexed"},
    ]

    ids = metadata_store.get_indexed_document_ids("owner-a")

    assert sorted(ids) == ["doc-1", "doc-3"]
    assert logged(db, "documents.find")[-1][1] == {"owner_id": "owner-a", "status": "indexed"}
    assert db.projections["documents"]["_id"] == 0


def test_a_delete_is_scoped_and_reports_whether_anything_went(db):
    assert metadata_store.delete_document("doc-1", "owner-a") is True
    db.deleted = 0
    assert metadata_store.delete_document("doc-9", "owner-a") is False

    assert logged(db, "documents.delete_one")[0][1] == {"document_id": "doc-1", "owner_id": "owner-a"}


def test_a_filename_backfill_is_one_query_not_one_per_chunk(db):
    db.rows["documents"] = [
        {"document_id": "doc-1", "owner_id": "owner-a", "filename": "loan.pdf"},
        # Requested by id, but owned by somebody else: the owner clause has to stay in the query.
        {"document_id": "doc-2", "owner_id": "owner-b", "filename": "not-yours.pdf"},
    ]

    names = metadata_store.filenames_for("owner-a", ["doc-1", "doc-2"])

    assert names == {"doc-1": "loan.pdf"}
    assert logged(db, "documents.find")[-1][1] == {
        "owner_id": "owner-a",
        "document_id": {"$in": ["doc-1", "doc-2"]},
    }


def test_an_empty_backfill_asks_the_database_nothing(db):
    assert metadata_store.filenames_for("owner-a", []) == {}
    assert db.log == []


def test_a_new_document_record_starts_as_uploading(db):
    """Written before parsing so a mid-crash upload stays visible as a stuck document rather than a
    vanishing one."""
    record = metadata_store.create_document_record(
        document_id="doc-1", owner_id="owner-a", filename="loan.pdf", content_type="application/pdf", size_bytes=2048
    )

    assert record["status"] == "uploading"
    assert record["page_count"] == 0
    assert record["uploaded_at"].tzinfo == timezone.utc
    assert logged(db, "documents.insert_one")[-1][1]["owner_id"] == "owner-a"


def test_a_new_conversation_gets_its_own_id_and_the_owner_that_asked_for_it(db):
    first = metadata_store.create_conversation("owner-a")
    second = metadata_store.create_conversation("owner-b", title="Loan questions")

    assert first["conversation_id"] != second["conversation_id"]
    assert first["title"] == "New chat"
    assert second["owner_id"] == "owner-b"


# ── messages ───────────────────────────────────────────────────────────────────


def test_saving_a_turn_also_bumps_its_conversation_and_carries_the_trace(db):
    metadata_store.save_message(
        owner_id="owner-a",
        conversation_id="conv-1",
        role="assistant",
        content="The fee is 250 dollars.",
        citations=[{"index": 1}],
        agent_trace=["router", "retrieval"],
    )

    inserted = logged(db, "messages.insert_one")[-1][1]
    assert inserted["citations"] == [{"index": 1}]
    assert inserted["agent_trace"] == ["router", "retrieval"]
    assert inserted["created_at"].tzinfo == timezone.utc

    touched = logged(db, "conversations.update_one")[-1]
    assert touched[1] == {"conversation_id": "conv-1", "owner_id": "owner-a"}
    assert "updated_at" in touched[2]["$set"]


def test_a_missing_citation_list_is_stored_empty_rather_than_null(db):
    """The frontend maps over citations; a null would throw in the browser for a benign old message."""
    record = metadata_store.save_message(owner_id="owner-a", conversation_id="conv-1", role="user", content="Hi?")

    assert record["citations"] == []
    assert record["agent_trace"] == []


def test_the_history_window_reads_the_latest_turns_and_returns_them_in_order(db):
    """Read newest-first so the limit keeps the *recent* tail, then reverse so the model sees the
    conversation chronologically. Getting either half wrong silently changes the context."""
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    db.rows["messages"] = [
        {"content": "q1", "owner_id": "owner-a", "conversation_id": "conv-1", "created_at": now},
        {"content": "q2", "owner_id": "owner-a", "conversation_id": "conv-1", "created_at": now.replace(minute=1)},
        {"content": "q3", "owner_id": "owner-a", "conversation_id": "conv-1", "created_at": now.replace(minute=2)},
        # Another user's turns in the same thread must not appear in this window.
        {"content": "x", "owner_id": "owner-b", "conversation_id": "conv-1", "created_at": now.replace(minute=3)},
    ]

    window = metadata_store.get_recent_messages("owner-a", "conv-1", limit=2)

    assert ("messages.sort", "created_at", -1) in db.log
    assert [row["content"] for row in window] == ["q2", "q3"]


def test_the_rendered_thread_is_chronological(db):
    db.rows["messages"] = [
        {
            "content": "later",
            "owner_id": "owner-a",
            "conversation_id": "conv-1",
            "created_at": datetime(2026, 11, 4, 2, tzinfo=timezone.utc),
        },
        {
            "content": "earlier",
            "owner_id": "owner-a",
            "conversation_id": "conv-1",
            "created_at": datetime(2026, 11, 4, 1, tzinfo=timezone.utc),
        },
    ]

    rows = metadata_store.get_conversation_messages("owner-a", "conv-1")

    assert ("messages.sort", "created_at", 1) in db.log
    assert [row["content"] for row in rows] == ["earlier", "later"]


def test_a_thread_list_is_scoped_and_newest_first(db):
    metadata_store.list_conversations("owner-a", limit=3)

    assert logged(db, "conversations.find")[-1][1] == {"owner_id": "owner-a"}
    assert ("conversations.sort", "updated_at", -1) in db.log
    assert ("conversations.limit", 3) in db.log


# ── degradation ────────────────────────────────────────────────────────────────


class _IndexBlindCollection:
    """A collection whose create_index raises, standing in for a cluster mid-failover."""

    def create_index(self, *args, **kwargs):
        raise OperationFailure("cluster is unavailable")


class _BlindDB:
    def __getitem__(self, name):
        return _IndexBlindCollection()


def test_index_creation_does_not_stop_the_app():
    """Atlas may be briefly unreachable during a cold start. A missing index slows reads; it does not
    corrupt them, so startup continues and the next boot retries."""
    metadata_store._ensure_indexes(_BlindDB())


def test_the_hot_paths_ask_for_the_owner_scoped_indexes(db):
    """The compound indexes mirror the filters above, in the same order: owner first, then the sort or
    the thread key. Without them, every list and every thread load is a collection scan."""
    metadata_store._ensure_indexes(db)

    specs = [str(entry[1][0]) for entry in db.log if entry[0].endswith("create_index")]

    assert len(specs) == 5
    assert any("owner_id" in s and "uploaded_at" in s for s in specs)
    assert any("owner_id" in s and "updated_at" in s for s in specs)
    assert any("conversation_id" in s and "created_at" in s for s in specs)
    assert any(s == "document_id" for s in specs)


def test_a_failed_ping_reports_down_instead_of_raising(monkeypatch):
    class Dead:
        def command(self, name):
            raise OperationFailure("no reachable member")

    monkeypatch.setattr(metadata_store, "_database", lambda: Dead())

    assert metadata_store.ping() is False


def test_a_successful_ping_reports_ready(monkeypatch):
    class Alive:
        def command(self, name):
            return {"ok": 1}

    monkeypatch.setattr(metadata_store, "_database", lambda: Alive())

    assert metadata_store.ping() is True
