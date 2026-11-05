"""
agents/graph.py - The LangGraph wiring that turns four agents into one pipeline.

    router --[search]--> retrieval --[hits]--> synthesis --> critique --[revise]--> synthesis
                          |                        \\--[finish]--> finalise --> END
                          +--[no hits]------------->/
           --[other]---> direct_reply --> END

WHY a StateGraph rather than four chained function calls, which is the first question a reviewer asks:

  * The critique loop is a cycle. `critique -> synthesis` cannot be expressed as a call chain without
    recursion and a hand-rolled counter; a conditional edge states it directly and the counter lives in
    the shared state.
  * Progress is observable per node. `stream_mode="updates"` yields each node's contribution as it
    finishes, which is what lets the SSE layer tell the user "Searching", "Drafting", "Verifying"
    instead of showing one spinner for an opaque wait.
  * State is declared, not implied. AgentState is the contract between nodes; chained calls pass
    positional arguments that drift as soon as a fifth agent is added.
  * Checkpointing and time-travel debugging come free with the runtime rather than being built by hand.

The compiled graph is cached at module level. Compiling rebuilds the node registry on every call, and a
per-request compile would add measurable latency to each turn for no benefit.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.critique_agent import review_answer
from app.agents.retrieval_agent import search_documents
from app.agents.router_agent import route_question
from app.agents.state import AgentState
from app.agents.synthesis_agent import (
    finalise,
    reply_without_search,
    should_revise,
)
from app.agents.synthesis_agent import draft_answer as synthesise

logger = logging.getLogger(__name__)


def after_router(state: AgentState) -> str:
    """Branch on the router's label.

    Anything other than an explicit `search` goes to the direct-reply path. Defaulting to the non-search
    branch here would be wrong in the opposite direction from the router's own fallback, so the two
    defaults are reconciled in one place: route_question already resolves ambiguity to "search", which
    means a missing key here is a bug rather than a judgement call.
    """
    return "retrieval" if state.get("route") == "search" else "direct"


def after_retrieval(state: AgentState) -> str:
    """Skip synthesis and critique when nothing was retrieved.

    An empty result is already an answer ("not in your documents"), so running a model over an empty
    context block would spend two Groq calls to produce a plausible unsourced paragraph. This edge is
    what turns that into zero calls.
    """
    return "synthesis" if state.get("chunks") else "finalise"


@lru_cache(maxsize=1)
def build_graph() -> Any:
    """Compile and cache the agent graph."""
    builder = StateGraph(AgentState)

    builder.add_node("router", route_question)
    builder.add_node("retrieval", search_documents)
    builder.add_node("synthesis", synthesise)
    builder.add_node("critique", review_answer)
    builder.add_node("direct", reply_without_search)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("router")
    builder.add_conditional_edges("router", after_router, {"retrieval": "retrieval", "direct": "direct"})
    builder.add_conditional_edges(
        "retrieval",
        after_retrieval,
        {"synthesis": "synthesis", "finalise": "finalise"},
    )
    builder.add_edge("synthesis", "critique")
    # The cycle: a failed review sends the draft back for one bounded revision.
    builder.add_conditional_edges(
        "critique",
        should_revise,
        {"revise": "synthesis", "finish": "finalise"},
    )
    builder.add_edge("finalise", END)
    builder.add_edge("direct", END)

    logger.debug("agent graph compiled")
    return builder.compile()


def run_turn(
    *,
    question: str,
    owner_id: str,
    document_ids: list[str],
    top_k: int,
    history: str,
) -> dict:
    """Execute one full agent turn and return the finished state.

    `invoke` runs the graph to completion. The SSE layer wants per-node progress, so it uses
    `stream` instead and calls this only as the fallback path; keeping both entry points honest is why
    the input defaults are assembled in `initial_state` rather than duplicated at each call site.
    """
    return build_graph().invoke(
        initial_state(
            question=question,
            owner_id=owner_id,
            document_ids=document_ids,
            top_k=top_k,
            history=history,
        )
    )


def stream_turn(inputs: dict) -> Any:
    """Yield (node_name, update) as each agent finishes.

    stream_mode="updates" is what makes multi-agent progress visible to the user: each tuple arrives the
    moment a node commits its state, so the frontend can say which agent is currently working rather than
    rendering one indeterminate spinner over a several-second wait.
    """
    return build_graph().stream(inputs, stream_mode="updates")


def initial_state(
    *,
    question: str,
    owner_id: str,
    document_ids: list[str],
    top_k: int,
    history: str,
) -> AgentState:
    """Build the input dict in one place.

    A missing default here shows up as a KeyError deep inside a node, which reads as an agent bug rather
    than a wiring bug. Centralising construction keeps that class of mistake in a single reviewable spot.
    """
    return {
        "question": question,
        "owner_id": owner_id,
        "document_ids": document_ids,
        "top_k": top_k,
        "history": history,
        "chunks": [],
        "citations": [],
        "attempts": 0,
        "issues": [],
        "trace": [],
    }
