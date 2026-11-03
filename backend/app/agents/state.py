"""
agents/state.py — The shared state that flows through the agent graph.

LangGraph passes one mutable state object between nodes; each node returns the keys it wants to update.
Typing that dict is the whole discipline of the design: an untyped state turns a four-agent pipeline into
four functions guessing at each other's keys, and a typo becomes a silently missing value at the far end
of the graph instead of an error at the node that introduced it.

Deliberate choices:

  * total=False. Nodes update a subset of keys; without this, TypedDict would demand the complete state
    on every return and every node would have to re-echo fields it did not touch.
  * attempts is a counter, not an implicit recursion limit. The critique loop has to be bounded
    explicitly, or a critic that never passes produces an infinite graph cycle.
  * trace is append-only. It is what the UI shows as progress and what a wrong answer is debugged from.
  * Chunks and citations travel as plain dicts rather than pydantic models. LangGraph merges state
    updates by key, and keeping the payload JSON-shaped avoids a subtle class of bug where a model
    instance is replaced by a dict halfway through the chain.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

RouteLabel = Literal["search", "chat", "unsafe", "clarify"]

# Bound on revision attempts. Two is a deliberate ceiling: the first rewrite fixes a real defect, a
# second is usually the critic and the model talking past each other, and a third spends Groq quota
# without changing the answer. Free-tier request limits make an unbounded loop a production hazard.
MAX_REVISIONS = 2


class AgentState(TypedDict, total=False):
    """Everything the agents read and write on their way to one answer."""

    # ── Inputs ──────────────────────────────────────────────────────────────────
    question: str
    history: str
    owner_id: str
    document_ids: list[str]
    top_k: int

    # ── Router output ───────────────────────────────────────────────────────────
    route: RouteLabel
    route_reason: str

    # ── Retrieval output ────────────────────────────────────────────────────────
    chunks: list[dict]
    citations: list[dict]

    # ── Synthesis output ────────────────────────────────────────────────────────
    draft: str
    attempts: int

    # ── Critique output ─────────────────────────────────────────────────────────
    verdict: Literal["pass", "fail", "skipped"]
    issues: list[str]

    # ── Final ───────────────────────────────────────────────────────────────────
    answer: str
    # Every node appends to trace, so it needs an explicit add reducer. LangGraph's default channel
    # behaviour is overwrite-by-latest-write: without operator.add, each node would erase the history and
    # the client would only ever see "finalise" - losing exactly the per-agent progress the SSE layer is
    # built to display.
    trace: Annotated[list[str], operator.add]
