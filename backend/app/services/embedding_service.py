"""
services/embedding_service.py — Local sentence embeddings with sentence-transformers.

WHY LOCAL: all-MiniLM-L6-v2 runs inside the FastAPI process, so embedding costs $0 and has no
rate limit. That matters here more than accuracy: Groq's free tier caps requests per minute and a
4-agent chain fires several LLM calls per question, so the one step we can run ourselves without a
quota we will. The trade is quality — MiniLM is a 384-dim model, weaker than hosted 1536-dim
embeddings — which is the right call for a free-tier portfolio build and is stated plainly rather
than hidden.

CRITICAL: documents and queries must be embedded by the SAME model. Vectors from different models
live in unrelated spaces, and cosine between them is meaningless. That is why both paths call
through here rather than instantiating their own model.

The model loads lazily into a module-level singleton. Loading takes a few seconds and ~90 MB of
RAM; Render's free tier gives 512 MB, so a second copy per request would be the first thing to
OOM the container.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
# A plain flag is not enough: two concurrent first requests would both see None and both load the
# model. The lock makes the second caller wait for the first instead of duplicating the load.
_load_lock = threading.Lock()


def _load_model() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    logger.info("loading embedding model %s", settings.embedding_model_name)
    model = SentenceTransformer(settings.embedding_model_name)
    actual_dim = model.get_sentence_embedding_dimension()
    if actual_dim != settings.embedding_dimension:
        # Catching this at load time beats getting an opaque Pinecone 400 on the first upload.
        raise RuntimeError(
            f"Embedding dimension mismatch: model '{settings.embedding_model_name}' produces "
            f"{actual_dim} dims but the index is configured for {settings.embedding_dimension}. "
            "Recreate the Pinecone index or change EMBEDDING_MODEL_NAME — never mix models."
        )
    logger.info("embedding model ready (%d dims)", actual_dim)
    return model


def get_model() -> SentenceTransformer:
    """Return the shared model instance, loading it on first use."""
    global _model
    if _model is None:
        with _load_lock:
            # Re-check inside the lock: another thread may have finished loading while we waited.
            if _model is None:
                _model = _load_model()
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of passages for indexing.

    Batched rather than one-at-a-time: the model vectorises over the whole matrix far faster than
    in a Python loop, and ingestion embeds a whole document in one call.
    """
    if not texts:
        return []
    vectors = get_model().encode(
        texts,
        # normalize_embeddings makes dot product equal cosine similarity, which is what Pinecone's
        # 'cosine' metric expects and what keeps scores comparable across chunks of different length.
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [vector.tolist() for vector in vectors]


def embed_query(text: str) -> list[float]:
    """Embed one search string. Kept separate from embed_texts only for call-site clarity.

    Identical model, identical normalisation — that is the invariant that makes the returned scores
    mean something.
    """
    return embed_texts([text])[0]
