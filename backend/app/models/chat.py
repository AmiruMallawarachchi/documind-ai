"""
models/chat.py — Request and response contracts for conversations and streaming answers.

The SSE event names are declared here as constants rather than typed inline in the router. The
frontend has to switch on exactly these strings, so one shared source of truth turns a rename from a
silent protocol break into a failing import.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.document import Citation


class ChatRequest(BaseModel):
    """One question against the user's indexed documents."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="The user's question. Long inputs are rejected rather than silently truncated.",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Existing thread. Omit to start a new one; the id comes back on the meta event.",
    )
    document_ids: list[str] | None = Field(
        default=None,
        description="Restrict retrieval to these documents. Omit to search everything indexed.",
    )
    top_k: int = Field(
        default=6,
        ge=1,
        le=20,
        description="How many passages to retrieve. Bounded so a client cannot ask for 5,000 and "
        "spend the whole free-tier token budget in one request.",
    )

    # max_length on question plus le on top_k make the two ways a caller can inflate cost explicit.
    model_config = {"json_schema_extra": {"example": {"question": "What is the late fee?"}}}


class ConversationResponse(BaseModel):
    conversation_id: str
    title: str
    updated_at: str


class MessageResponse(BaseModel):
    role: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    agent_trace: list[str] = Field(default_factory=list)
    created_at: str


class ConversationDetail(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]


# ── SSE protocol ────────────────────────────────────────────────────────────────
# Event names the frontend switches on.
EVENT_META = "meta"
EVENT_CITATIONS = "citations"
EVENT_TOKEN = "token"
EVENT_TRACE = "trace"
EVENT_STATUS = "status"
EVENT_DONE = "done"
EVENT_ERROR = "error"
