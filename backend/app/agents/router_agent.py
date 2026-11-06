"""
agents/router_agent.py — Decide what kind of turn this is before spending a retrieval or a synthesis.

WHY a router agent at all: three of the four branches need no documents, and running retrieval anyway
costs an embedding call, a Pinecone query, and a prompt full of irrelevant text. Worse, feeding
"how are you?" a page of contract clauses invites the model to answer a greeting with legal boilerplate.
Routing first keeps each downstream agent doing one job on inputs that make sense for it.

The fallback is deliberately biased toward `search`. Misclassifying a real document question as chit-chat
denies the user an answer they needed; misclassifying a greeting as a search wastes one vector query. The
first failure is user-visible and the second is not, so the ambiguous case goes to the expensive-but-correct
branch.

That bias only works if the classifier knows what the question refers to. People write "what is this
document?" and "summarise it" when they have one file selected, and a router given only the words cannot
see the referent - it reads a demonstrative as vagueness and CLARIFYs a perfectly clear request. The
scoped filenames therefore travel with the question.
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import AgentState
from app.services import metadata_store
from app.services.llm import LLMUnavailableError, complete_text
from app.services.prompts import ROUTER_SYSTEM_PROMPT, router_prompt

logger = logging.getLogger(__name__)

_VALID_ROUTES = {"search", "chat", "unsafe", "clarify"}

# Either of the two words the model has been seen using for its label line.
_LABEL_LINE = re.compile(r"^\s*(?:label|route)\s*:\s*(.*)$", re.IGNORECASE | re.MULTILINE)

# How many filenames the scope line lists before it switches to counting the rest. Six short names is well
# inside the 48-token budget, and a user with two hundred documents does not need them read out to classify
# one question.
_MAX_SCOPES_LISTED = 6


def describe_scope(owner_id: str, document_ids: list[str]) -> str:
    """Name the documents a question is being asked against.

    The router classifies the turn before retrieval runs, so it has no chunks and therefore no filenames
    of its own. This is the one small read that fixes that, and it is only made for the scoped ids the
    caller already resolved.
    """
    if not document_ids:
        return "Not restricted to particular files - every document this user has indexed is in scope."

    names = metadata_store.filenames_for(owner_id, document_ids)
    total = len(document_ids)
    if total == 1:
        return f"1 document: {names.get(document_ids[0], 'an unnamed document')}"

    listed = ", ".join(names.get(doc_id, "an unnamed document") for doc_id in document_ids[:_MAX_SCOPES_LISTED])
    if total > _MAX_SCOPES_LISTED:
        return f"{total} documents: {listed}, and {total - _MAX_SCOPES_LISTED} more"
    return f"{total} documents: {listed}"


def parse_route(raw: str) -> tuple[str, str]:
    """Extract the label and reason from the model's reply.

    Tolerant on purpose: an instruction-following model sometimes answers "LABEL: SEARCH" and sometimes
    "search" or "Route: SEARCH - needs the contract", so anything unrecognisable falls back to `search`
    rather than crashing the turn.

    The label line is read on its own when the reply has one. Scanning the whole reply instead lets the
    reason override the label it belongs to, and "LABEL: CLARIFY / REASON: user wants to search" then
    routes as a search.
    """
    text = raw.strip()
    reason = ""
    for line in text.splitlines():
        if line.lower().startswith("reason:"):
            reason = line.split(":", 1)[1].strip()

    label_line = _LABEL_LINE.search(text)
    haystack = (label_line.group(1) if label_line else text).lower()

    # The earliest route word wins, and matching is whole-word so "research" cannot be read as "search".
    # Taking the first hit from iterating the set instead would be arbitrary: a set's order comes from the
    # hashes, so two processes running one identical reply could route it two different ways.
    hits = [
        (match.start(), candidate)
        for candidate in _VALID_ROUTES
        if (match := re.search(rf"\b{candidate}\b", haystack)) is not None
    ]
    if hits:
        route = min(hits)[1]
        return route, reason or route
    return "search", reason or "defaulted to search"


def route_question(state: AgentState) -> dict:
    """Classify the question and append the decision to the trace.

    The scoped filenames go into the prompt alongside the question. Classifying a turn without knowing
    what it refers to is what made "what is this document?" - against one indexed document, an unambiguous
    request - come back CLARIFY and refuse the user with a prompt to be more specific.

    A router failure must not sink the whole turn: the model may be rate-limited. If classification is
    unavailable we proceed as a search rather than returning an error the user cannot act on, since the
    retrieval and synthesis path already handles "no relevant context" gracefully.
    """
    try:
        raw = complete_text(
            [
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(
                    content=router_prompt(
                        question=state["question"],
                        scope=describe_scope(state["owner_id"], list(state.get("document_ids") or [])),
                    )
                ),
            ],
            # A classification needs no creativity, and one token budget keeps the cheapest call possible.
            temperature=0.0,
            max_tokens=48,
        )
    except LLMUnavailableError as exc:
        logger.warning("router unavailable, defaulting to search: %s", exc)
        return {"route": "search", "route_reason": "router unavailable", "trace": ["router:fallback"]}

    route, reason = parse_route(raw)
    logger.info("routed question as %s (%s)", route, reason)
    return {"route": route, "route_reason": reason, "trace": [f"router:{route}"]}
