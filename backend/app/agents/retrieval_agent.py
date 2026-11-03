"""
agents/retrieval_agent.py — Fetch the evidence, and stop early when there is none.

Separating retrieval from synthesis is what makes the critique possible: the critic needs the exact
passages the answer was built from, and if one function did both there would be nothing to check the
draft against.

The zero-hit branch is the important behaviour here. Handing the synthesis agent an empty context block
and asking it to answer anyway is how a RAG product produces a confident, unsourced paragraph — and it
costs a Groq call to do it. Returning the honest "not found" reply early is both cheaper and better.
"""

from __future__ import annotations

import logging

from app.agents.state import AgentState
from app.services import prompts, retrieval

logger = logging.getLogger(__name__)


def search_documents(state: AgentState) -> dict:
    """Embed the question, query Pinecone, and stage chunks plus citations onto the state."""
    chunks = retrieval.retrieve(
        owner_id=state["owner_id"],
        question=state["question"],
        top_k=state.get("top_k") or retrieval.DEFAULT_TOP_K,
        document_ids=state.get("document_ids") or None,
    )
    # Filename backfill has to happen before citations are built, or a re-uploaded document is cited
    # under a name the user no longer has.
    chunks = retrieval.resolve_filenames(state["owner_id"], chunks)
    citations = retrieval.citations_from(chunks)

    if not chunks:
        # Short-circuit here rather than letting synthesis face an empty context block. Asking the model
        # to answer with no evidence is how a RAG system produces a fluent, unsourced guess - and it costs
        # a Groq call to buy that guess. The graph routes straight to finalise on this signal.
        logger.info("retrieval agent found nothing; skipping synthesis and critique")
        return {
            "chunks": [],
            "citations": [],
            "answer": prompts.NO_CONTEXT_ANSWER,
            "verdict": "skipped",
            "trace": ["retrieval:0"],
        }

    logger.info("retrieval agent found %d passages", len(chunks))
    return {
        "chunks": [chunk.model_dump() for chunk in chunks],
        "citations": [citation.model_dump() for citation in citations],
        "trace": [f"retrieval:{len(chunks)}"],
    }
