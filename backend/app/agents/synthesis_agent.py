"""
agents/synthesis_agent.py - Draft the cited answer, revise it when the critic rejects it, and handle
the turns that need no documents at all.

The draft is buffered rather than streamed to the client. This is the central tension of the design and
the choice is deliberate: the promise is that the critique agent checks the draft against the retrieved
passages *before anything reaches the user*, which is impossible while tokens are still travelling to the
browser. The verified answer is then replayed to the UI in chunks, so progressive rendering is preserved,
and the status events emitted while the graph runs make the wait legible instead of a silent spinner.

On a revision pass the critic's specific complaints are appended to the prompt. Without them, "draft
again" is a second roll of the same dice and would very likely reproduce the same unsupported claim that
was just rejected.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.router_agent import describe_scope
from app.agents.state import MAX_REVISIONS, AgentState
from app.models.document import RetrievedChunk
from app.services import prompts, retrieval
from app.services.llm import LLMUnavailableError, complete_text

logger = logging.getLogger(__name__)

# The sentinel the system prompt tells the model to emit when the passages do not support an answer. It
# must be detected rather than displayed: leaking "NOT_FOUND_IN_CONTEXT" into the UI turns an honest
# refusal into a protocol error the user cannot read.
NOT_FOUND_SENTINEL = "NOT_FOUND_IN_CONTEXT"

UNSAFE_REPLY = (
    "That request is outside what this assistant does. It reads and answers questions about documents "
    "you upload, and nothing else."
)


def _clarify_reply(state: AgentState) -> str:
    """Ask which document the user means, and show them the list they are choosing from.

    The scope is printed rather than described: "name the file" is useless advice to someone who cannot
    see the filenames from where they are typing, and a made-up example ("try the loan agreement") teaches
    them to ask about a document they may not have.
    """
    scope = describe_scope(state["owner_id"], list(state.get("document_ids") or []))
    return (
        "I could not work out what to look for, so I did not want to guess. These are in scope:\n\n"
        + scope
        + "\n\nAsk about one of them by name or by topic - a clause, a figure, a page - and I will read it."
    )


def _context_block(state: AgentState) -> str:
    """Rebuild the numbered passages from state.

    Chunks travel as dicts because LangGraph merges state by key, while the prompt formatter wants
    models. The conversion happens at this boundary and nowhere else.
    """
    chunks = [RetrievedChunk(**row) for row in state.get("chunks", [])]
    if not chunks:
        return "(no retrieved context)"
    return retrieval.build_context_block(chunks)


def draft_answer(state: AgentState) -> dict:
    """Produce the answer draft from the retrieved passages."""
    attempts = int(state.get("attempts", 0)) + 1
    issues = state.get("issues") or []
    extra = "; ".join(issues) if issues else None

    prompt = prompts.synthesis_prompt(
        context=_context_block(state),
        question=state["question"],
        history=state.get("history") or "(this is the first turn)",
        extra_instruction=extra,
    )

    try:
        draft = complete_text(
            [
                SystemMessage(content=prompts.SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ],
            # Low temperature: the task is "say what the documents say and cite it", where creativity is
            # hallucination. A hotter setting yields fluent paraphrase the passages do not support, which
            # the critic would rightly reject - wasting the call that got us there.
            temperature=0.2,
            max_tokens=1024,
        )
    except LLMUnavailableError as exc:
        logger.error("synthesis failed: %s", exc)
        # The answer is set here so the graph can finish with something honest instead of throwing away
        # the retrieval work already done. verdict "skipped" stops the critique loop retrying a failure.
        return {
            "draft": "",
            "attempts": attempts,
            "answer": (
                "The language model is rate-limited or unreachable right now, so I could not read your "
                "documents. Please try again in a moment."
            ),
            "verdict": "skipped",
            "issues": [],
            "trace": ["synthesis:error"],
        }

    if NOT_FOUND_SENTINEL in draft.upper():
        # The passages genuinely do not cover the question. Saying so plainly is the correct outcome, not
        # a failure to be retried - hence the substitution before the critic ever sees it.
        draft = prompts.NO_CONTEXT_ANSWER

    logger.info("synthesis produced %d chars on attempt %d", len(draft), attempts)
    return {"draft": draft, "attempts": attempts, "trace": [f"synthesis:{attempts}"]}


def reply_without_search(state: AgentState) -> dict:
    """Answer a turn the router decided needs no documents.

    Kept separate from draft_answer because there is no context block to ground on and no critic to
    verify against: a greeting has no citations to check, so routing it through the verification path
    would spend two model calls to confirm nothing.
    """
    route = state.get("route", "chat")

    if route == "unsafe":
        return {"answer": UNSAFE_REPLY, "trace": ["direct:unsafe"]}

    if route == "clarify":
        return {"answer": _clarify_reply(state), "trace": ["direct:clarify"]}

    try:
        reply = complete_text(
            [
                SystemMessage(
                    content=(
                        "You are DocuMind, a document analysis assistant. The user is not asking about "
                        "their documents. Reply in at most two sentences, and do not invent document "
                        "content. Steer them back to asking about their files when it fits."
                    )
                ),
                HumanMessage(content=state["question"]),
            ],
            temperature=0.4,
            max_tokens=128,
        )
    except LLMUnavailableError as exc:
        logger.warning("direct reply unavailable: %s", exc)
        reply = (
            "I can answer questions about the documents you upload. Upload a PDF, DOCX, or TXT file and "
            "ask away."
        )

    return {"answer": reply, "trace": ["direct:chat"]}


def should_revise(state: AgentState) -> str:
    """Route after the critic: revise, or accept.

    The ceiling is enforced here rather than inside the critic, so the loop bound lives in one place and
    cannot be forgotten when the graph is edited. Without it, a critic that always fails and a synthesiser
    that always answers the same way form an endless cycle that drains the free-tier token budget.
    """
    if state.get("verdict") in ("pass", "skipped"):
        return "finish"
    if int(state.get("attempts", 0)) >= MAX_REVISIONS:
        logger.info("revision ceiling hit at %d attempts, accepting the draft", state.get("attempts"))
        return "finish"
    return "revise"


def finalise(state: AgentState) -> dict:
    """Move the accepted draft into the answer slot.

    Its own node so the graph has exactly one terminal writer of `answer`. Reading it from anywhere else
    would mean two sources of truth for what the user was actually told.
    """
    answer = state.get("answer") or state.get("draft") or ""
    if not answer.strip():
        answer = "Something went wrong while preparing the answer. Please try again."
    return {"answer": answer.strip(), "trace": ["finalise"]}
