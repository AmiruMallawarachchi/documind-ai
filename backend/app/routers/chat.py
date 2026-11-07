"""
routers/chat.py — Conversations, and the SSE stream that delivers grounded answers.

This router is the presentation layer for the agent graph in app/agents/. It owns two things and nothing
else: the HTTP/SSE contract the frontend switches on, and persistence of each turn. All the reasoning —
routing, retrieval, drafting, verification — lives in the nodes, so swapping an agent or re-ordering the
graph never touches the wire format.

WHY Server-Sent Events rather than a plain JSON response or WebSockets: an LLM answer arrives over
several seconds, and a user staring at a spinner cannot tell working from hung. SSE gives the browser
native `EventSource` semantics over plain HTTP, needs no upgrade handshake (Render's free tier and
most proxies handle it fine, whereas WebSocket upgrades are a common source of deployment breakage), and
is one-directional — which is exactly the shape of "stream me the answer". WebSockets would add
connection bookkeeping for a channel that only ever sends one way.

The event contract is defined in models/chat.py and is shared with the frontend.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from app.agents import graph
from app.core.deps import chat_rate_limit, current_owner_id
from app.models.chat import (
    ChatRequest,
    ConversationDetail,
    ConversationPatch,
    ConversationResponse,
    EVENT_CITATIONS,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_META,
    EVENT_STATUS,
    EVENT_TOKEN,
    MessageResponse,
)
from app.services import metadata_store, prompts

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# SSE headers that make streaming actually stream.
# X-Accel-Buffering: no tells nginx-derived proxies (Render sits behind one) not to buffer the whole
# response before forwarding — without it the user receives every token at once at the end and the
# streaming work is invisible.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# What each agent is doing, in the user's words. The keys are the graph's node names, so adding a fifth
# agent without a label degrades to showing the node name rather than breaking the stream.
_STAGE_LABELS = {
    "router": "Reading your question",
    "retrieval": "Searching your documents",
    "synthesis": "Drafting the answer",
    "critique": "Checking the answer against your sources",
    "direct": "Replying",
    "finalise": "Preparing your answer",
}


def _sse(event: str, data: object) -> str:
    """Serialise one SSE frame.

    `data:` must be a single line or split across several, so payloads are compact JSON rather than
    pretty-printed. The trailing blank line is what terminates a frame; omit it and the client waits
    forever for an event that already arrived.
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _existing_conversation(owner_id: str, request: ChatRequest) -> dict | None:
    """Load a named thread, or 404. Never creates one."""
    if not request.conversation_id:
        return None
    conversation = metadata_store.get_conversation(owner_id, request.conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found for this user.",
        )
    return conversation


def _start_conversation(owner_id: str, request: ChatRequest) -> dict:
    """Open a thread for a question that is actually going to be answered.

    Title is seeded from the question because there is nothing better to call it by, and an untitled
    "New chat" list is useless once a user has four threads.
    """
    title = request.question.strip().replace("\n", " ")[:60] or "New chat"
    return metadata_store.create_conversation(owner_id, title=title)


@router.post("/chat")
async def ask(
    request: ChatRequest,
    owner_id: str = Depends(current_owner_id),
    _: None = Depends(chat_rate_limit),
) -> StreamingResponse:
    """Answer one question over SSE: meta, citations, tokens, then done.

    A synchronous generator is returned rather than an async one. Everything inside it — embedding,
    Pinecone, Mongo, Groq — is blocking I/O, and Starlette runs a sync iterator on the threadpool, so
    each in-flight stream occupies a worker thread instead of freezing the event loop for every other
    request on the instance. An async generator here would block the loop on the first Groq call and
    the health endpoint would time out alongside it.
    """
    # A named thread is validated before the empty-corpus guard, so a bad id still 404s. Creating the
    # thread happens only afterwards: a first-time visitor who is told to upload something should not
    # leave behind an untitled conversation that no message ever belongs to.
    conversation = _existing_conversation(owner_id, request)
    indexed_ids = metadata_store.get_indexed_document_ids(owner_id)
    if not indexed_ids:
        return _plain_no_documents_response()

    selected = request.document_ids
    if selected:
        # Validation happens before streaming starts, because once the first SSE frame is sent the status
        # line is already committed: a 4xx becomes impossible mid-stream and the client would just see a
        # truncated body.
        # Silently dropping a stale or unindexed id would let the user believe they asked about a
        # document that was never searched, so refuse and name it.
        unknown = [doc_id for doc_id in selected if doc_id not in indexed_ids]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "These documents are not indexed yet (upload may still be running, or it failed): "
                    + ", ".join(unknown)
                ),
            )
        scope = selected
    else:
        scope = indexed_ids

    # The thread is opened only once the request is known to be answerable. Creating it up front left an
    # untitled conversation behind on every rejected question.
    conversation = conversation or _start_conversation(owner_id, request)
    conversation_id = str(conversation["conversation_id"])

    history_rows = metadata_store.get_recent_messages(owner_id, conversation_id, limit=8)
    history = prompts.format_history(
        [{"role": row.get("role", "user"), "content": row.get("content", "")} for row in history_rows]
    )

    generator = _stream_answer(
        owner_id=owner_id,
        conversation_id=conversation_id,
        question=request.question.strip(),
        document_ids=scope,
        top_k=request.top_k,
        history=history,
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=_SSE_HEADERS)


def _plain_no_documents_response() -> StreamingResponse:
    """Tell the user to upload something, using the same SSE contract as a real answer.

    One protocol for both outcomes means the frontend needs no special case for an empty corpus, and a
    first-time visitor gets guidance instead of a validation error.
    """
    message = (
        "You have no indexed documents yet. Upload a PDF, DOCX, or TXT file and wait until its status "
        "shows as indexed, then ask again."
    )

    def frames() -> Iterator[str]:
        yield _sse(EVENT_META, {"conversation_id": None, "retrieved": 0})
        yield _sse(EVENT_CITATIONS, [])
        yield _sse(EVENT_TOKEN, {"text": message})
        yield _sse(EVENT_DONE, {"answer": message, "agent_trace": ["guard:empty-corpus"]})

    return StreamingResponse(frames(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _replay(answer: str, *, words_per_frame: int = 6) -> Iterator[str]:
    """Re-send the verified answer in token-sized frames.

    The draft is buffered so the critic can check it before the user sees a word of it, which means the
    streaming this endpoint promises is a replay rather than a live tap into the model. Splitting on word
    boundaries with the trailing whitespace kept inside each piece makes the concatenation exactly equal the
    answer, so a client that renders incrementally and a client that waits for `done` show the same text.
    """
    words = re.findall(r"\S+\s*", answer)
    for start in range(0, len(words), words_per_frame):
        yield "".join(words[start : start + words_per_frame])


def _stream_answer(
    *,
    owner_id: str,
    conversation_id: str,
    question: str,
    document_ids: list[str],
    top_k: int,
    history: str,
) -> Iterator[str]:
    """Drive one turn of the agent graph and render it as SSE.

    The frame contract is unchanged from the single-agent version — meta, citations, tokens, done — which
    is what makes the multi-agent upgrade invisible to the frontend. Two things are added on top: a `status`
    frame per node, because verification means several seconds of work before the first token and an
    unlabelled wait reads as a hang, and the agent trace in `done`, because "the critic rejected the draft
    twice" is the information a user needs when an answer looks evasive.
    """
    metadata_store.save_message(owner_id=owner_id, conversation_id=conversation_id, role="user", content=question)

    # `stream_mode="updates"` gives each node's delta, not the merged state, so the accumulation is done
    # here. `trace` is append-only because the graph merges it with operator.add; every other key is a
    # plain overwrite. Reimplementing the reducer wrong here would show a trace missing the revision passes.
    merged: dict = {"trace": []}
    citations_payload: list[dict] = []
    meta_sent = False

    def remember(update: dict) -> None:
        for key, value in update.items():
            if key == "trace":
                merged["trace"] = [*merged["trace"], *(value or [])]
            else:
                merged[key] = value

    try:
        for node_update in graph.stream_turn(
            graph.initial_state(
                question=question,
                owner_id=owner_id,
                document_ids=document_ids,
                top_k=top_k,
                history=history,
            )
        ):
            for node, update in node_update.items():
                remember(update)
                yield _sse(
                    EVENT_STATUS,
                    {"stage": node, "label": _STAGE_LABELS.get(node, node), "agents": list(merged["trace"])},
                )
                # Every path visits retrieval or direct, and one of them always emits the header frames
                # before the first token arrives: the UI needs the source chips rendered while it types.
                if not meta_sent and node in ("retrieval", "direct"):
                    citations_payload = list(merged.get("citations") or [])
                    yield _sse(
                        EVENT_META,
                        {"conversation_id": conversation_id, "retrieved": len(merged.get("chunks") or [])},
                    )
                    yield _sse(EVENT_CITATIONS, citations_payload)
                    meta_sent = True

        answer = str(merged.get("answer") or "").strip()
        for delta in _replay(answer):
            yield _sse(EVENT_TOKEN, {"text": delta})

    except Exception as exc:
        logger.exception("agent stream failed conversation=%s", conversation_id)
        # The user's message is already stored, so store the failure next to it. A thread that shows the
        # question with no reply looks like a lost answer on refresh, and the retry hint belongs in the
        # transcript rather than only in the ephemeral stream.
        notice = "I could not finish that answer — the search or the model failed. Please try again."
        metadata_store.save_message(
            owner_id=owner_id,
            conversation_id=conversation_id,
            role="assistant",
            content=notice,
            agent_trace=[*merged["trace"], f"error:{type(exc).__name__}"],
        )
        yield _sse(EVENT_ERROR, {"message": f"Unexpected error: {type(exc).__name__}"})
        return

    trace = list(merged["trace"])

    # One structured line per turn, keyed to the request id the client also received. When someone says
    # "the answer was wrong", this is what lets you find the run and see whether the critic passed it.
    logger.info(
        "chat turn completed",
        extra={
            "conversation_id": conversation_id,
            "retrieved": len(merged.get("chunks") or []),
            "agent_steps": len(trace),
            "answer_characters": len(answer),
        },
    )

    # Persist before telling the client we are done. A refresh should always show the full exchange,
    # and saving after the closing frame would let the stream finish while the write is still pending.
    metadata_store.save_message(
        owner_id=owner_id,
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        citations=citations_payload,
        agent_trace=trace,
    )
    yield _sse(EVENT_DONE, {"answer": answer, "agent_trace": trace, "citations": citations_payload})


def _conversation_response(row: dict) -> ConversationResponse:
    """Project a thread header onto the API, in one place.

    The list and the detail view used to spell this projection separately, which is how a new field
    silently appears in one and not the other.
    """
    return ConversationResponse(
        conversation_id=str(row["conversation_id"]),
        title=str(row.get("title", "New chat")),
        updated_at=metadata_store.to_iso(row.get("updated_at")),
        pinned=bool(row.get("pinned", False)),
    )


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(owner_id: str = Depends(current_owner_id)) -> list[ConversationResponse]:
    rows = metadata_store.list_conversations(owner_id)
    return [_conversation_response(row) for row in rows]


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def patch_conversation(
    conversation_id: str,
    request: ConversationPatch,
    owner_id: str = Depends(current_owner_id),
) -> ConversationResponse:
    """Rename and/or pin a thread.

    A patch that names no field is refused rather than answered with the current row: a caller that sent
    `{}` has lost its payload somewhere, and 200 would hide that.
    """
    fields: dict = {}
    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A conversation needs a title.",
            )
        fields["title"] = title
    if request.pinned is not None:
        fields["pinned"] = bool(request.pinned)

    if not fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing to change: send a title, a pinned flag, or both.",
        )

    if not metadata_store.update_conversation(owner_id, conversation_id, **fields):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found for this user.",
        )

    header = metadata_store.get_conversation(owner_id, conversation_id)
    return _conversation_response(header)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    owner_id: str = Depends(current_owner_id),
) -> Response:
    """Delete a thread with every message in it. There is no archive and no trash.

    `-> Response` for the same reason `documents.delete_document` uses it: annotating `-> None` makes
    NoneType the response model and the route then fails its own 204 at import time.
    """
    if not metadata_store.delete_conversation(owner_id, conversation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found for this user.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    owner_id: str = Depends(current_owner_id),
) -> ConversationDetail:
    header = metadata_store.get_conversation(owner_id, conversation_id)
    if header is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    rows = metadata_store.get_conversation_messages(owner_id, conversation_id)
    messages = [
        MessageResponse(
            role=str(row.get("role", "user")),
            content=str(row.get("content", "")),
            citations=[c for c in row.get("citations", [])],
            agent_trace=[str(step) for step in row.get("agent_trace", [])],
            created_at=metadata_store.to_iso(row.get("created_at")),
        )
        for row in rows
    ]
    return ConversationDetail(conversation=_conversation_response(header), messages=messages)
