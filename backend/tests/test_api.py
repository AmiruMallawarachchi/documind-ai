"""
tests/test_api.py — HTTP contract: status codes, owner isolation, and the SSE event sequence.

These are the behaviours the frontend depends on. The SSE test parses the wire format rather than
calling internal functions, because the bug class worth catching here is protocol drift — an event
renamed or reordered and the UI silently stops streaming.
"""

from __future__ import annotations

import io
import json


from app.core.config import settings
from tests.conftest import make_chunk
from tests.test_extractor import build_pdf

OWNER_A = {"X-Demo-Key": "owner-a"}
OWNER_B = {"X-Demo-Key": "owner-b"}


def parse_sse(stream) -> list[tuple[str, dict]]:
    """Turn an SSE stream into (event, payload) pairs. Accepts a Response or its body text.

    Deliberately strict about the format: a frame missing its blank-line terminator is a real bug that
    would leave the browser waiting forever, and this parser surfaces it instead of papering over it.
    """
    body = getattr(stream, "text", stream)
    events: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        name, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        assert name is not None, f"frame without an event name: {frame!r}"
        assert data is not None, f"event {name} without a data line"
        events.append((name, json.loads(data)))
    return events


def upload(client, filename="loan.pdf", data=None, headers=OWNER_A, content_type="application/pdf"):
    payload = data if data is not None else build_pdf(["The late fee is 250 dollars per occurrence."])
    return client.post(
        "/api/upload",
        headers=headers,
        files=[("files", (filename, io.BytesIO(payload), content_type))],
    )


def test_health_reports_version(client):
    from app.core.config import settings

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": settings.app_version}


def test_liveness_stays_up_when_a_dependency_is_down(client, monkeypatch):
    """Render restarts a container that fails its health check. If Atlas hiccupped on /api/health, one
    slow query would become a restart loop — so this route must not look at anything but itself."""
    from app.services import metadata_store

    def explode():
        raise RuntimeError("no reachable member in replica set")

    monkeypatch.setattr(metadata_store, "ping", explode)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_deep_health_names_each_dependency(client, monkeypatch):
    from app.routers import health
    from app.services import metadata_store

    monkeypatch.setattr(metadata_store, "ping", lambda: True)
    monkeypatch.setattr(health, "_pinecone_reachable", lambda: True)

    body = client.get("/api/health/deep").json()

    assert body["services"] == {"mongodb": "up", "pinecone": "up"}
    assert body["index"]


def test_a_down_dependency_is_reported_without_failing_the_request(client, monkeypatch):
    """200 with the truth beats 503 with a status code: this endpoint exists to be read by a human."""
    from app.routers import health
    from app.services import metadata_store

    monkeypatch.setattr(metadata_store, "ping", lambda: False)
    monkeypatch.setattr(health, "_pinecone_reachable", lambda: False)

    response = client.get("/api/health/deep")

    assert response.status_code == 200
    assert response.json()["services"] == {"mongodb": "down", "pinecone": "down"}


def test_deep_health_never_echoes_a_driver_error_message(client, monkeypatch):
    """ pymongo and Pinecone both put the connection string — with the password — into their exception
    text, and this endpoint is unauthenticated."""
    from app.routers import health
    from app.services import metadata_store

    def refuse():
        raise RuntimeError("auth failed for secret-password")

    monkeypatch.setattr(metadata_store, "ping", refuse)
    monkeypatch.setattr(health, "_pinecone_reachable", lambda: True)

    body = client.get("/api/health/deep").text

    assert "secret-password" not in body


def test_a_probe_that_never_returns_is_cut_off(client, monkeypatch):
    """A hung socket must not hold the request open: the ceiling is the whole point of a timeout."""
    import time as _time

    from app.routers import health
    from app.services import metadata_store

    monkeypatch.setattr(health, "_DEEP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(metadata_store, "ping", lambda: _time.sleep(1.0))
    monkeypatch.setattr(health, "_pinecone_reachable", lambda: True)

    assert client.get("/api/health/deep").json()["services"]["mongodb"] == "timeout"


def test_upload_accepts_and_indexes_in_background(client, fake_metadata, fake_vectors):
    """202, not 200: indexing is queued, and the client polls for the result."""
    response = upload(client)
    assert response.status_code == 202

    body = response.json()
    assert len(body["documents"]) == 1
    assert body["documents"][0]["status"] in {"uploading", "indexed"}

    document_id = body["documents"][0]["document_id"]
    listing = client.get("/api/documents", headers=OWNER_A).json()
    assert [row["document_id"] for row in listing] == [document_id]


def test_upload_rejects_unsupported_extension(client):
    response = upload(client, filename="notes.md", data=b"# hello", content_type="text/markdown")
    assert response.status_code == 415


def test_upload_rejects_oversized_body(client):
    from app.core.config import settings

    oversized = b"x" * (settings.max_upload_bytes + 10)
    response = upload(client, filename="big.txt", data=oversized, content_type="text/plain")
    assert response.status_code == 413


def test_upload_rejects_empty_file(client):
    response = upload(client, filename="blank.txt", data=b"", content_type="text/plain")
    assert response.status_code == 400


def test_upload_requires_a_filename(client):
    """A part with no filename is rejected outright.

    Asserting on the range rather than one code: FastAPI rejects a filename-less multipart part with
    422 during parsing, before my handler's own 400 can fire. Both mean "never reached storage", which is
    the property that matters; pinning a single code would couple the test to framework internals.
    """
    response = client.post(
        "/api/upload",
        headers=OWNER_A,
        files=[("files", ("", io.BytesIO(b"data"), "text/plain"))],
    )
    assert response.status_code in (400, 422)


def test_documents_are_isolated_per_owner(client, fake_metadata, fake_vectors):
    """The single most important security property in the app.

    One user seeing another's file list — or, worse, another's document text in an answer — is a breach
    regardless of how good the retrieval quality is. This is asserted at both stores.
    """
    upload(client, filename="alpha.pdf", data=build_pdf(["Alpha private terms."]))
    upload(client, filename="beta.pdf", data=build_pdf(["Beta private terms."]), headers=OWNER_B)

    a_listing = client.get("/api/documents", headers=OWNER_A).json()
    b_listing = client.get("/api/documents", headers=OWNER_B).json()
    assert [row["filename"] for row in a_listing] == ["alpha.pdf"]
    assert [row["filename"] for row in b_listing] == ["beta.pdf"]

    # Direct id access must also 404 across owners, not 403: revealing existence is itself a leak.
    foreign_id = b_listing[0]["document_id"]
    assert client.get(f"/api/documents/{foreign_id}", headers=OWNER_A).status_code == 404


def test_retrieval_always_filters_by_owner(client, fake_metadata, fake_vectors, fake_embedding, fake_llm):
    """The owner filter has to be applied at the query level, not post-filtered.

    Post-filtering would still pull another user's text out of Pinecone and into the process, so the
    assertion here is on what the query carried.
    """
    upload(client, data=build_pdf(["Repayment is due in 36 months."]))
    fake_vectors["canned_results"] = [make_chunk()]

    response = client.post(
        "/api/chat",
        headers=OWNER_A,
        json={"question": "When is repayment due?", "top_k": 4},
    )
    assert response.status_code == 200

    query = fake_vectors["queries"][-1]
    assert query["owner_id"] == "owner-a"
    assert query["top_k"] == 4


def test_chat_stream_emits_the_documented_event_sequence(client, fake_metadata, fake_vectors, fake_llm):
    upload(client, data=build_pdf(["The late fee is 250 dollars."]))
    fake_vectors["canned_results"] = [make_chunk()]

    response = client.post("/api/chat", headers=OWNER_A, json={"question": "What is the late fee?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # Without this header, a buffering proxy delivers every token at once and streaming is invisible.
    assert response.headers["x-accel-buffering"] == "no"

    events = parse_sse(response.text)
    names = [name for name, _ in events]

    # Relative order, not absolute positions: the graph emits a status frame per agent, and how many
    # arrive before `meta` depends on the route taken. What the frontend cannot tolerate is a token that
    # reaches it before the conversation id, or a done frame that closes the stream early.
    assert names.index("meta") < names.index("citations") < names.index("token") < len(names) - 1
    assert names[-1] == "done"
    assert names[0] == "status", "the first frame must name the agent working, not leave the UI guessing"

    answer = "".join(payload["text"] for name, payload in events if name == "token")
    assert answer == "The fee is 250 dollars"
    assert events[-1][1]["answer"] == answer, "the replayed tokens and the final answer must agree exactly"


def test_status_events_name_the_agent_at_work(client, fake_metadata, fake_vectors, fake_llm):
    """Verification costs seconds; the user gets a reason for each one rather than one spinner."""
    upload(client, data=build_pdf(["The late fee is 250 dollars."]))
    fake_vectors["canned_results"] = [make_chunk()]

    events = parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "What is the late fee?"}))
    stages = [payload["stage"] for name, payload in events if name == "status"]

    assert stages == ["router", "retrieval", "synthesis", "critique", "finalise"]
    assert all(payload["label"] for name, payload in events if name == "status")


def test_route_without_documents_skips_retrieval_and_verification(client, fake_metadata, fake_vectors, fake_llm):
    """"Hello" must not cost an embedding call, a vector query, and a critic pass."""
    upload(client, data=build_pdf(["Terms."]))
    fake_llm["route"] = "LABEL: CHAT\nREASON: greeting"
    fake_llm["answer"] = "Hello! Ask me about your documents."

    events = parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "hi"}))
    stages = [payload["stage"] for name, payload in events if name == "status"]

    assert fake_vectors["queries"] == [], "a chit-chat turn searched the corpus"
    assert stages == ["router", "direct"], f"retrieval or critique ran on a greeting: {stages}"
    assert events[-1][1]["citations"] == []
    assert events[-1][1]["answer"] == "Hello! Ask me about your documents."
    assert "synthesis" not in fake_llm["kinds"]


def test_citations_are_numbered_and_match_the_prompt_anchors(client, fake_metadata, fake_vectors, fake_llm):
    """A citation offset by one points the reader at a passage that does not support the claim."""
    upload(client, data=build_pdf(["Terms of the agreement."]))
    fake_vectors["canned_results"] = [
        make_chunk(document_id="doc-1", page=3, text="First source passage."),
        make_chunk(document_id="doc-2", page=7, text="Second source passage."),
    ]

    events = dict(parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "Terms?"})))
    citations = events["citations"]

    assert [citation["index"] for citation in citations] == [1, 2]
    assert citations[0]["page_number"] == 3
    assert citations[1]["document_id"] == "doc-2"
    assert all(citation["snippet"] for citation in citations)


def test_chat_with_no_indexed_documents_guides_instead_of_erroring(client, fake_metadata):
    """A first-time visitor must get instructions, and must not spend a Groq call on an empty corpus."""
    events = dict(parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "Hello?"})))

    assert events["meta"]["retrieved"] == 0
    assert events["citations"] == []
    # The token payload is {"text": ...}, matching the shape the frontend appends from.
    guidance = events["done"]["answer"].lower()
    assert "upload" in guidance and "index" in guidance


def test_chat_without_retrieval_hits_never_calls_the_model(client, fake_metadata, fake_vectors, fake_llm):
    """Below the score floor there is no evidence, and asking a model to answer anyway produces a
    fluent, unsourced guess."""
    upload(client, data=build_pdf(["Something."]))
    fake_vectors["canned_results"] = []

    events = dict(parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "Unrelated topic?"})))

    # The router may still run - it cannot know there is nothing to find - but no draft and no verdict.
    assert "synthesis" not in fake_llm["kinds"], "synthesis ran against an empty context block"
    assert "critique" not in fake_llm["kinds"]
    assert "token" in events
    assert events["done"]["citations"] == []
    assert events["done"]["agent_trace"][-1] == "finalise"


def test_search_failure_becomes_an_in_band_error_event(client, fake_metadata, fake_vectors, fake_llm):
    """Once SSE frames are flowing the status line is committed at 200, so errors must travel in-band."""
    upload(client, data=build_pdf(["Terms."]))
    fake_vectors["canned_results"] = [make_chunk()]
    fake_vectors["raise_query"] = RuntimeError("index unavailable")

    events = parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "Terms?"}))

    assert any(name == "error" for name, _ in events)
    assert not any(name == "done" for name, _ in events)
    assert all("message" in payload for name, payload in events if name == "error")


def test_model_outage_degrades_to_a_readable_answer_not_a_broken_stream(client, fake_metadata, fake_vectors, fake_llm):
    """A rate-limited Groq is the common failure, and each agent absorbs its own share of it.

    The user should still get a closed stream with an honest sentence in it. An error frame here would be
    the worse outcome: it tells them nothing about what to do next.
    """
    from app.services.llm import LLMUnavailableError

    upload(client, data=build_pdf(["Terms."]))
    fake_vectors["canned_results"] = [make_chunk()]
    fake_llm["raises"] = LLMUnavailableError("429 rate limited")

    events = parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "Terms?"}))
    names = [name for name, _ in events]

    assert names[-1] == "done"
    assert "error" not in names
    answer = events[-1][1]["answer"]
    assert "rate-limit" in answer.lower() or "try again" in answer.lower()


def test_conversation_history_is_persisted_with_the_agent_trace(client, fake_metadata, fake_vectors, fake_llm):
    """Stored so a wrong answer can be debugged, and so a refresh shows the whole exchange."""
    upload(client, data=build_pdf(["Terms."]))
    fake_vectors["canned_results"] = [make_chunk()]

    first = dict(parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "What are the terms?"})))
    conversation_id = first["meta"]["conversation_id"]
    assert conversation_id

    client.post("/api/chat", headers=OWNER_A, json={"question": "And the fee?", "conversation_id": conversation_id})

    detail = client.get(f"/api/conversations/{conversation_id}", headers=OWNER_A).json()
    roles = [message["role"] for message in detail["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert all("agent_trace" in message for message in detail["messages"])
    assert detail["messages"][1]["citations"], "an assistant turn that cited passages must store them"


def test_conversation_is_not_readable_across_owners(client, fake_metadata, fake_vectors, fake_llm):
    upload(client, data=build_pdf(["Private."]))
    fake_vectors["canned_results"] = [make_chunk()]
    first = dict(parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": "Private?"})))

    stolen = first["meta"]["conversation_id"]
    assert client.get(f"/api/conversations/{stolen}", headers=OWNER_B).status_code == 404


def start_thread(client, fake_vectors, question="What are the terms?") -> str:
    """One indexed document and one answered question, which is what makes a real thread id."""
    upload(client, data=build_pdf(["Terms."]))
    fake_vectors["canned_results"] = [make_chunk()]
    events = dict(parse_sse(client.post("/api/chat", headers=OWNER_A, json={"question": question})))
    return events["meta"]["conversation_id"]


def test_a_thread_can_be_renamed_and_pinned(client, fake_metadata, fake_vectors, fake_llm):
    conversation_id = start_thread(client, fake_vectors)

    response = client.patch(
        f"/api/conversations/{conversation_id}",
        headers=OWNER_A,
        json={"title": "Vendor agreement terms", "pinned": True},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Vendor agreement terms"
    assert response.json()["pinned"] is True

    listed = client.get("/api/conversations", headers=OWNER_A).json()
    assert [row["title"] for row in listed] == ["Vendor agreement terms"]
    assert listed[0]["pinned"] is True


def test_a_thread_starts_unpinned(client, fake_metadata, fake_vectors, fake_llm):
    """Nothing in the corpus was written with a `pinned` key, so the list must not invent one."""
    conversation_id = start_thread(client, fake_vectors)

    assert client.get(f"/api/conversations/{conversation_id}", headers=OWNER_A).json()[
        "conversation"
    ]["pinned"] is False


def test_a_title_that_says_nothing_is_refused(client, fake_metadata, fake_vectors, fake_llm):
    """`{}` means a caller lost its payload, and a whitespace title is the same accident."""
    conversation_id = start_thread(client, fake_vectors)

    assert client.patch(
        f"/api/conversations/{conversation_id}", headers=OWNER_A, json={}
    ).status_code == 422
    assert client.patch(
        f"/api/conversations/{conversation_id}", headers=OWNER_A, json={"title": "   "}
    ).status_code == 422
    assert client.get(f"/api/conversations/{conversation_id}", headers=OWNER_A).json()[
        "conversation"
    ]["title"] == "What are the terms?"


def test_deleting_a_thread_takes_the_whole_exchange(client, fake_metadata, fake_vectors, fake_llm):
    conversation_id = start_thread(client, fake_vectors)
    assert fake_metadata.messages, "the thread under test must actually have rows to lose"

    assert client.delete(f"/api/conversations/{conversation_id}", headers=OWNER_A).status_code == 204

    assert client.get("/api/conversations", headers=OWNER_A).json() == []
    assert fake_metadata.messages == []
    assert client.get(f"/api/conversations/{conversation_id}", headers=OWNER_A).status_code == 404


def test_a_thread_cannot_be_renamed_or_deleted_by_another_owner(client, fake_metadata, fake_vectors, fake_llm):
    """The id is a Mongo ObjectId and the owner header is the only other thing the request carries."""
    conversation_id = start_thread(client, fake_vectors)

    assert client.patch(
        f"/api/conversations/{conversation_id}", headers=OWNER_B, json={"title": "Stolen"}
    ).status_code == 404
    assert client.delete(f"/api/conversations/{conversation_id}", headers=OWNER_B).status_code == 404

    assert client.get(f"/api/conversations/{conversation_id}", headers=OWNER_A).json()[
        "conversation"
    ]["title"] == "What are the terms?"


def test_an_unknown_thread_cannot_be_patched_or_deleted(client, fake_metadata):
    assert client.patch(
        "/api/conversations/conv-does-not-exist", headers=OWNER_A, json={"pinned": True}
    ).status_code == 404
    assert client.delete("/api/conversations/conv-does-not-exist", headers=OWNER_A).status_code == 404


def shared_view(client, token):
    """Read a share link the way a stranger does: no headers at all."""
    return client.get(f"/api/shared/{token}")


def test_sharing_a_thread_gives_one_stable_link(client, fake_metadata, fake_vectors, fake_llm):
    """Pressing Share twice must not retire the first link, which would leave it live and unrevokeable."""
    conversation_id = start_thread(client, fake_vectors)

    first = client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).json()
    second = client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).json()

    assert first == second
    assert first["share_id"]


def test_a_shared_thread_needs_no_session_to_read(client, fake_metadata, fake_vectors, fake_llm):
    conversation_id = start_thread(client, fake_vectors)
    token = client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).json()["share_id"]

    body = shared_view(client, token).json()

    assert body["title"] == "What are the terms?"
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["content"]


def test_a_shared_thread_shows_answers_only(client, fake_metadata, fake_vectors, fake_llm):
    """The whole promise of the link: prose, and nothing that names the owner's documents.

    Asserted against the serialised body rather than the model, because the field that must not appear
    is the one a later refactor forgets to strip.
    """
    conversation_id = start_thread(client, fake_vectors)
    token = client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).json()["share_id"]

    assert fake_metadata.messages[1].get("citations"), "the thread under test must have citations to hide"

    response = shared_view(client, token)
    body = response.text

    for secret in ["loan.pdf", "citations", "chunk_text", "page_number", "owner_id", "conversation_id", "agent_trace"]:
        assert secret not in body, f"{secret} must not reach a public page"


def test_revoking_a_link_closes_it(client, fake_metadata, fake_vectors, fake_llm):
    conversation_id = start_thread(client, fake_vectors)
    token = client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).json()["share_id"]
    assert shared_view(client, token).status_code == 200

    assert client.delete(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).status_code == 204

    assert shared_view(client, token).status_code == 404
    assert client.get(f"/api/conversations/{conversation_id}", headers=OWNER_A).status_code == 200


def test_another_owner_cannot_share_or_unshare_a_thread(client, fake_metadata, fake_vectors, fake_llm):
    conversation_id = start_thread(client, fake_vectors)

    assert client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_B).status_code == 404

    token = client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).json()["share_id"]
    assert client.delete(f"/api/conversations/{conversation_id}/share", headers=OWNER_B).status_code == 404
    assert shared_view(client, token).status_code == 200


def test_an_unknown_or_absurd_token_is_404(client, fake_metadata):
    """A token that is too long to be one is refused as absent, never as malformed."""
    assert shared_view(client, "tok-does-not-exist").status_code == 404
    assert shared_view(client, "x" * 500).status_code == 404


def test_the_list_says_which_threads_are_shared(client, fake_metadata, fake_vectors, fake_llm):
    """The sidebar needs the flag without the token, so the token stays out of a cached list."""
    conversation_id = start_thread(client, fake_vectors)

    assert client.get("/api/conversations", headers=OWNER_A).json()[0]["shared"] is False

    client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A)

    listed = client.get("/api/conversations", headers=OWNER_A).json()[0]
    assert listed["shared"] is True
    assert "share_id" not in listed


def test_shared_reads_are_limited_per_address(client, fake_metadata, fake_vectors, fake_llm, monkeypatch):
    """The one route with no identity to rate limit, so the address is the only handle there is."""
    monkeypatch.setattr(settings, "shared_requests_per_minute", 2)
    conversation_id = start_thread(client, fake_vectors)
    token = client.post(f"/api/conversations/{conversation_id}/share", headers=OWNER_A).json()["share_id"]

    assert [shared_view(client, token).status_code for _ in range(4)] == [200, 200, 429, 429]


def test_chat_validates_document_scope(client, fake_metadata):
    """A stale or unindexed id must be refused by name, not silently dropped from the search."""
    upload(client, data=build_pdf(["Indexed doc."]))

    response = client.post(
        "/api/chat",
        headers=OWNER_A,
        json={"question": "Anything?", "document_ids": ["still-uploading-or-forever-gone"]},
    )
    assert response.status_code == 400
    assert "not indexed" in response.json()["detail"].lower()


def test_a_rejected_question_leaves_no_orphan_conversation(client, fake_metadata, fake_vectors):
    """A thread the user never sees is worse than no thread: it fills the history list with blanks.

    Covers both refusals - the empty-corpus guidance and the invalid document scope.
    """
    client.post("/api/chat", headers=OWNER_A, json={"question": "Anything?"})
    upload(client, data=build_pdf(["Indexed doc."]))
    client.post(
        "/api/chat",
        headers=OWNER_A,
        json={"question": "Anything?", "document_ids": ["never-indexed"]},
    )

    assert fake_metadata.conversations == {}
    assert fake_metadata.messages == []


def test_an_unknown_conversation_is_404_even_with_an_empty_corpus(client, fake_metadata):
    """Refusing to answer is not the same as not finding the thread the user asked to continue."""
    response = client.post(
        "/api/chat",
        headers=OWNER_A,
        json={"question": "Continue?", "conversation_id": "conv-does-not-exist"},
    )
    assert response.status_code == 404


def test_question_length_is_bounded(client, fake_metadata):
    """Cost control: an unbounded question would inflate the prompt against the daily token limit."""
    response = client.post("/api/chat", headers=OWNER_A, json={"question": "x" * 9000})
    assert response.status_code == 422


def test_top_k_is_clamped(client, fake_metadata):
    response = client.post("/api/chat", headers=OWNER_A, json={"question": "Hi", "top_k": 5000})
    assert response.status_code == 422


def test_blank_demo_key_is_rejected(client):
    """An empty header must not silently become the default user — that would defeat isolation tests."""
    assert client.get("/api/documents", headers={"X-Demo-Key": "   "}).status_code == 400


def test_delete_removes_document_from_listing(client, fake_metadata, fake_vectors):
    document_id = upload(client, data=build_pdf(["Terms."])).json()["documents"][0]["document_id"]

    assert client.delete(f"/api/documents/{document_id}", headers=OWNER_A).status_code == 204
    assert client.get("/api/documents", headers=OWNER_A).json() == []
    assert client.delete(f"/api/documents/{document_id}", headers=OWNER_A).status_code == 404


def test_status_summary_counts_by_state(client, fake_metadata, fake_vectors):
    upload(client, data=build_pdf(["One."]))
    upload(client, filename="two.pdf", data=build_pdf(["Two."]))

    summary = client.get("/api/documents/status/summary", headers=OWNER_A).json()
    assert summary["indexed"] == 2
