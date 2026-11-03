"""
services/prompts.py — The instruction text that makes retrieval into a grounded answer.

Prompts live in one module rather than inline in services so they can be reviewed, diffed, and A/B
tested without hunting through logic. Every template here states its contract in the docstring because
the downstream parser depends on that shape.

The grounding rule shared by all of them: answer only from the supplied context, cite with [n]
markers, and say you do not know when the context does not cover it. An ungrounded model answers
plausibly rather than correctly, and in a document-Q&A product a plausible wrong answer about a
contract term is the worst outcome available.
"""

from __future__ import annotations

from app.models.document import Citation

# A refusal, not a hallucination. Naming the gap lets the user rephrase or upload the missing document
# instead of wondering whether a smooth paragraph was actually supported.
NO_CONTEXT_ANSWER = (
    "I could not find anything about that in your indexed documents, so I will not guess. "
    "If the answer should be in a specific file, check that it finished indexing and is not a "
    "scanned/image-only PDF (optical character recognition is not supported in this version)."
)

SYSTEM_PROMPT = """\
You are DocuMind, a careful analyst who answers questions about documents.

Answer using ONLY the numbered context passages provided. Never use outside knowledge to fill a gap,
even if you recognise the subject.

Rules:
1. Every factual claim must cite its source with the bracketed passage number, e.g. [2] or [1][3].
2. If several passages are relevant, cite each one that supports part of the answer.
3. If the passages do not contain the answer, reply exactly: NOT_FOUND_IN_CONTEXT
4. Never invent names, dates, figures, or clause numbers that are not in the passages.
5. Be concise and direct. Plain prose, no preamble like "Based on the documents".
6. Use the same language as the question.
"""

SYNTHESIS_TEMPLATE = """\
{system}

CONTEXT PASSAGES:
{context}

CONVERSATION SO FAR:
{history}

QUESTION:
{question}

INSTRUCTIONS:
Answer the question from the passages above. Cite every claim with [n] markers matching the passage
numbers. If the passages do not support an answer, reply exactly NOT_FOUND_IN_CONTEXT.
"""

ROUTER_SYSTEM_PROMPT = """\
You classify a user question about their documents. Reply with EXACTLY one line, no prose:

SEARCH      - needs the documents to answer (facts, terms, summaries, comparisons between files)
CHAT        - greeting, small talk, or a question about the assistant itself
UNSAFE      - asks for something harmful, illegal, or outside document analysis
CLARIFY     - too vague to search (no discernible subject, or a pronoun with no clear referent)

Format:
LABEL: <one of the four>
REASON: <up to 12 words>
"""

CRITIQUE_SYSTEM_PROMPT = """\
You are a verification auditor. Check a draft answer against the source passages it was required to use.

Fail the draft if ANY of these hold:
- It states something the passages do not say (invented figure, date, name, or clause).
- It carries a citation marker that does not actually support the sentence it is attached to.
- It answers a different question than the one asked.
- It omits an obvious contradiction present in the passages.
- It is missing citation markers on factual claims.

Pass if the claims are supported and attributed, even if the wording could be smoother. Style is not a
failure reason.

Reply with EXACTLY:
VERDICT: PASS or VERDICT: FAIL
ISSUES: <one short line per problem, or NONE>
"""


def synthesis_prompt(
    *,
    context: str,
    question: str,
    history: str = "(this is the first turn)",
    extra_instruction: str | None = None,
) -> str:
    """Build the grounded answer prompt.

    extra_instruction carries the critic's feedback on a retry, so the revision is aimed at a specific
    defect instead of being a fresh roll of the same prompt.
    """
    prompt = SYNTHESIS_TEMPLATE.format(
        system=SYSTEM_PROMPT,
        context=context,
        history=history,
        question=question,
    )
    if extra_instruction:
        prompt += f"\n\nPREVIOUS ATTEMPT WAS REJECTED:\n{extra_instruction}\nFix that specific problem.\n"
    return prompt


def format_history(messages: list[dict[str, str]], max_turns: int = 6) -> str:
    """Render prior turns for the prompt, oldest first.

    Only the most recent turns are included: a long thread otherwise grows the prompt every round and
    eventually spends the whole daily token allowance on history. Assistant replies are stripped of
    citation markers because [3] from an earlier round refers to that round's passage numbering, and
    leaving them in teaches the model to cite against indices that do not exist now.
    """
    import re

    recent = messages[-max_turns:] if max_turns else messages
    if not recent:
        return "(this is the first turn)"

    lines: list[str] = []
    for message in recent:
        role = "User" if message.get("role") == "user" else "Assistant"
        content = str(message.get("content", "")).strip()
        if role == "Assistant":
            content = re.sub(r"\[\d+(?:\]\[\d+)*\]", "", content).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(this is the first turn)"


def citations_footnote(citations: list[Citation]) -> str:
    """Render the source list appended beneath an answer.

    Emitted as text as well as structured JSON so a copied answer keeps its attribution even outside
    the UI that would otherwise render the citation chips.
    """
    if not citations:
        return ""
    lines = [
        f"[{citation.index}] {citation.filename}, page {citation.page_number}"
        for citation in citations
    ]
    return "\n\nSOURCES:\n" + "\n".join(lines)
