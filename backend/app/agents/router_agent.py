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
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import AgentState
from app.services.llm import LLMUnavailableError, complete_text
from app.services.prompts import ROUTER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_VALID_ROUTES = {"search", "chat", "unsafe", "clarify"}


def parse_route(raw: str) -> tuple[str, str]:
    """Extract the label and reason from the model's reply.

    Tolerant on purpose: an instruction-following model sometimes answers "LABEL: SEARCH" and sometimes
    "search" or "Route: SEARCH - needs the contract". A brittle parser would turn that variety into a
    crash, so the label is found by scanning for a known word rather than by trusting the format, and
    anything unrecognisable falls back to `search`.
    """
    text = raw.strip().lower()
    reason = ""
    for line in text.splitlines():
        if line.startswith("reason:"):
            reason = line.split(":", 1)[1].strip()

    for candidate in _VALID_ROUTES:
        if candidate in text:
            return candidate, reason or candidate
    return "search", reason or "defaulted to search"


def route_question(state: AgentState) -> dict:
    """Classify the question and append the decision to the trace.

    A router failure must not sink the whole turn: the model may be rate-limited. If classification is
    unavailable we proceed as a search rather than returning an error the user cannot act on, since the
    retrieval and synthesis path already handles "no relevant context" gracefully.
    """
    try:
        raw = complete_text(
            [
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=state["question"]),
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
