"""
services/retrieval.py — Turn a natural-language question into ranked, citable evidence.

Retrieval is the half of RAG that decides whether the answer can be correct at all. The LLM can only
ground itself in what this module hands it, so the rules here are about protecting that set:

  1. Scope by owner, always. vector_store.query_similar applies the owner_id filter on every call.
  2. Drop weak matches rather than padding to top_k. Six chunks where three score well means three
     real pieces of evidence plus three distractions, and the model will happily cite a distraction.
  3. Collapse duplicate near-identical chunks. Overlap seeding means a boundary sentence can appear
     in two consecutive chunks; retrieving both wastes a slot the user paid for.
  4. Say "not found" loudly. Returning an empty list is a meaningful answer, not an error to paper over.
  5. Re-rank once, unfiltered, when a *narrow* scope returns nothing. A question about a document as a
     whole shares no vocabulary with the passages inside it, so the floor calls the right file noise; see
     `retrieve`.

The score floor is a cosine-similarity threshold tuned for all-MiniLM-L6-v2, where unrelated text
typically lands below 0.20 and on-topic text sits around 0.35+. It is a default, not a constant —
expose it per request so it can be tuned against real documents.
"""

from __future__ import annotations

import logging

from app.models.document import Citation, RetrievedChunk
from app.services import metadata_store
from app.services.embedding_service import embed_query
from app.services.vector_store import query_similar

logger = logging.getLogger(__name__)

# Below this cosine score a chunk is noise for MiniLM-class models. Raising it trades recall for
# precision; a value near zero would send garbage into the prompt and invite a confidently wrong answer.
DEFAULT_SCORE_FLOOR = 0.18

# Retrieval depth. Small on purpose: more context is not better context once the model has enough to
# answer, and every extra chunk costs prompt tokens against Groq's free-tier daily limit.
DEFAULT_TOP_K = 6

# How many documents may be in scope before a zero-hit search is allowed to fall back to unfiltered
# ranking. Three is a guess with a reason behind it: a person asking about one, two or three open files is
# almost certainly asking about *those* files, while a question aimed at a whole library that returns
# nothing is most likely asking about something the library does not contain.
_OVERVIEW_SCOPE_MAX_DOCUMENTS = 3


def _deduplicate(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Drop chunks whose text is an exact repeat of a higher-scoring neighbour.

    Chunks are already score-sorted, so keeping the first occurrence keeps the best-scoring copy.
    Compared on normalised text so trailing whitespace differences do not hide a duplicate.
    """
    seen: set[str] = set()
    unique: list[RetrievedChunk] = []
    for chunk in chunks:
        key = " ".join(chunk.chunk_text.split()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


def _ranked(
    *,
    owner_id: str,
    query_vector: list[float],
    top_k: int,
    document_ids: list[str] | None,
    score_floor: float | None,
) -> tuple[list[RetrievedChunk], int]:
    """One vector query, de-duplicated, in score order — plus how many matches arrived before that.

    The raw count is returned separately because the difference between the two is the deduplication the
    overlap seeding produces, which is the number worth seeing in a log when passages look repeated.
    """
    chunks = query_similar(
        owner_id=owner_id,
        query_vector=query_vector,
        top_k=top_k,
        document_ids=document_ids,
        score_threshold=score_floor,
    )
    return _deduplicate(chunks), len(chunks)


def retrieve(
    *,
    owner_id: str,
    question: str,
    top_k: int = DEFAULT_TOP_K,
    document_ids: list[str] | None = None,
    score_floor: float = DEFAULT_SCORE_FLOOR,
) -> list[RetrievedChunk]:
    """Embed the question and return the chunks that should ground the answer.

    The query goes through the same model and normalisation as indexing — that is the invariant that
    makes the cosine scores mean anything. Mixing embedding models here would produce technically
    valid vectors in an unrelated space and silently destroy retrieval quality.

    A second, unfiltered attempt covers the questions similarity search is structurally blind to. "What
    is this document?" is a request about a file as a whole and therefore shares no vocabulary with any
    passage inside it; measured against the exact document it asks about, this scores 0.08, well under the
    floor. Ranking without the floor still puts that document's own passages first, so the fallback costs
    one extra query and returns evidence rather than a refusal.

    It is limited to a narrow scope on purpose. The floor exists to keep irrelevant text out of the prompt,
    and with a whole library in scope a zero-hit search genuinely means "not in your documents" - lowering
    it there would paste noise into the prompt and buy a confidently wrong answer, which is the exact
    failure the floor was introduced to prevent.
    """
    if not question.strip():
        return []

    query_vector = embed_query(question)
    hits, matches = _ranked(
        owner_id=owner_id,
        query_vector=query_vector,
        top_k=top_k,
        document_ids=document_ids,
        score_floor=score_floor,
    )

    if not hits and document_ids and len(document_ids) <= _OVERVIEW_SCOPE_MAX_DOCUMENTS:
        unfiltered, _ = _ranked(
            owner_id=owner_id,
            query_vector=query_vector,
            top_k=top_k,
            document_ids=document_ids,
            score_floor=None,
        )
        if unfiltered:
            logger.info(
                "overview fallback owner=%s scope=%d hits=%d",
                owner_id,
                len(document_ids),
                len(unfiltered),
            )
            return unfiltered

    logger.info(
        "retrieved owner=%s question_chars=%d hits=%d after_dedupe=%d floor=%.2f",
        owner_id,
        len(question),
        matches,
        len(hits),
        score_floor,
    )
    return hits


def build_context_block(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks for the prompt, with numbered anchors the model can cite.

    WHY numbered tags instead of raw paragraphs: the model needs something to point at, and a visible
    [1], [2], [3] marker per passage is what lets it emit "..." [2] rather than a claim we cannot
    trace. It also makes the ordering stable between the synthesis and critique passes, so the
    critic and the answer agree on what [2] refers to.

    Each block carries document name and page so attribution survives even if the model cites loosely.
    """
    if not chunks:
        return "(no retrieved context)"

    blocks: list[str] = []
    for position, chunk in enumerate(chunks, start=1):
        source = f"{chunk.filename}, page {chunk.page_number}" if chunk.page_number else chunk.filename
        blocks.append(f"[{position}] (source: {source})\n{chunk.chunk_text}")
    return "\n\n---\n\n".join(blocks)


def citations_from(chunks: list[RetrievedChunk]) -> list[Citation]:
    """Attach a 1-based numbered citation to each retrieved chunk.

    The numbering matches build_context_block exactly, because both iterate the same list in the same
    order. Keeping them in one module is deliberate: a citation offset by one is worse than no
    citation, since it points the reader at a passage that does not support the claim.

    snippet is truncated: it is preview text for the UI tooltip, not the evidence itself, and the full
    chunk is already what the model saw.
    """
    return [
        Citation(
            index=position,
            document_id=chunk.document_id,
            filename=chunk.filename,
            chunk_id=chunk.chunk_id,
            page_number=chunk.page_number,
            snippet=chunk.chunk_text[:220].strip(),
        )
        for position, chunk in enumerate(chunks, start=1)
    ]


def resolve_filenames(owner_id: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Refresh filename metadata from Mongo for chunks whose stored name is stale or missing.

    Pinecone metadata is written at upload time. If a document was later renamed or re-uploaded, the
    vector still carries the old label, and a citation naming a file the user cannot see is worse than
    one that says "unknown". Re-reads are cheap: it is one $in query, not one per chunk.
    """
    missing = [chunk.document_id for chunk in chunks if not chunk.filename or chunk.filename == "unknown"]
    if not missing:
        return chunks

    names = metadata_store.filenames_for(owner_id, list(set(missing)))
    if not names:
        # Mongo may be down or the document deleted; keep the existing labels rather than failing the answer.
        logger.warning("filename backfill found no documents for %d ids", len(set(missing)))
        return chunks

    return [
        chunk.model_copy(update={"filename": names[chunk.document_id]})
        if chunk.document_id in names and (not chunk.filename or chunk.filename == "unknown")
        else chunk
        for chunk in chunks
    ]
