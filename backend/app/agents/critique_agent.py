"""
agents/critique_agent.py - Verify the draft against the passages before the user sees it.

This node is what separates the project from a plain retrieve-then-generate chain. A single-pass RAG
system cannot notice that its own answer is unsupported, so it ships the hallucination with a citation
that looks authoritative - the most damaging failure mode for a document Q&A product, because the reader
has just been handed a reason to trust it.

Two rules keep the loop honest:

  * Judge attribution, not style. "Could be smoother" is not a defect, and a critic that rejects prose
    quality produces an expensive rewrite of an answer that was already correct.
  * Fail open. If the critic itself cannot run, the draft passes. Blocking a good answer because a
    verification call was rate-limited turns an infrastructure problem into a worse user-facing one.
    The trace records that verification was skipped, so a pass is never mistaken for a real one.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import AgentState
from app.models.document import RetrievedChunk
from app.services import prompts, retrieval
from app.services.llm import LLMUnavailableError, complete_text

logger = logging.getLogger(__name__)


def parse_verdict(raw: str) -> tuple[str, list[str]]:
    """Read PASS/FAIL and the issue lines out of the critic's reply.

    Unparseable output is treated as a pass, not a fail. A critic that returns unexpected text must not
    spin the revision loop: failing on garbage would burn another synthesis call to very likely meet the
    same unreadable response.
    """
    text = raw.strip()
    lowered = text.lower()

    verdict = "fail" if "fail" in lowered else "pass"

    issues: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("verdict"):
            continue
        if stripped.lower().startswith("issues"):
            remainder = stripped.split(":", 1)[-1].strip()
            if remainder and remainder.lower() not in {"none", "no", "n/a", "-"}:
                issues.append(remainder)
        elif verdict == "fail":
            issues.append(stripped)

    return verdict, issues[:4]


def review_answer(state: AgentState) -> dict:
    """Check every claim in the draft against the retrieved passages."""
    chunk_rows = state.get("chunks", [])
    if not chunk_rows:
        # Nothing was retrieved, so there is nothing to verify against: the draft is already the honest
        # "not found" reply. Spending a model call to confirm that would be pure cost.
        return {"verdict": "skipped", "issues": [], "trace": ["critique:skipped"]}

    draft = state.get("draft", "")
    if not draft.strip():
        return {"verdict": "skipped", "issues": ["empty draft"], "trace": ["critique:skipped"]}

    chunks = [RetrievedChunk(**row) for row in chunk_rows]
    context = retrieval.build_context_block(chunks)

    review_prompt = (
        f"QUESTION:\n{state['question']}\n\n"
        f"SOURCE PASSAGES:\n{context}\n\n"
        f"DRAFT ANSWER:\n{draft}\n\n"
        "Return VERDICT and ISSUES only."
    )

    try:
        raw = complete_text(
            [
                SystemMessage(content=prompts.CRITIQUE_SYSTEM_PROMPT),
                HumanMessage(content=review_prompt),
            ],
            # Zero temperature: a verification step must not be creative about what counts as evidence.
            temperature=0.0,
            max_tokens=256,
        )
    except LLMUnavailableError as exc:
        logger.warning("critique unavailable, letting the draft through: %s", exc)
        return {"verdict": "skipped", "issues": [], "trace": ["critique:unavailable"]}

    verdict, issues = parse_verdict(raw)
    logger.info("critique verdict=%s issues=%d", verdict, len(issues))
    return {"verdict": verdict, "issues": issues, "trace": [f"critique:{verdict}"]}
