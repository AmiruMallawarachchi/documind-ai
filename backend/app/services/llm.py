"""
services/llm.py — Groq chat model access, streaming, and rate-limit resilience.

Groq's free tier caps requests per minute and tokens per day, and a four-agent chain fires several
calls per user question. Without backoff, a single busy minute turns into visible 429 errors in the
chat UI. The retry rule lives in one wrapper here rather than at each call site, so every agent added
in Phase 3 inherits it automatically.

Only transient failures are retried. A 400 (bad prompt, over-length) or a 401 (bad key) is a bug in our
code or config, and retrying it burns quota while delaying the real error.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_groq import ChatGroq

from app.core.config import settings

logger = logging.getLogger(__name__)

# Groq's free-tier ceiling is per-model and moves without notice; treat these as a starting point and
# re-check console.groq.com/docs/rate-limits before tuning.
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 1024

# Three attempts is enough for a real 429 window to pass. More would make the user wait too long
# behind Render's free-tier request timeout to be worth the extra quota.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 20.0


class LLMUnavailableError(RuntimeError):
    """Every attempt failed, or the failure was not retryable."""


def _retryable(exc: BaseException) -> bool:
    """Classify an upstream error as worth another attempt.

    Duck-typed on the status code rather than importing groq exception classes: langchain-groq wraps
    some errors, and matching on the number the provider sent is more durable than matching on which
    library raised first.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        # Wrapped exceptions keep the original on .__cause__; check it before giving up.
        cause = getattr(exc, "__cause__", None)
        status = getattr(cause, "status_code", None) if cause is not None else None

    if status is None:
        text = str(exc).lower()
        # Some clients only surface the reason as text; 429/503/overloaded are the retryable shapes.
        return any(marker in text for marker in ("429", "rate limit", "503", "overloaded", "timed out"))

    return status in (429, 500, 502, 503, 504)


def _sleep_before(attempt: int) -> float:
    """Exponential backoff with jitter.

    WHY jitter: retries scheduled on identical intervals fire together from every worker after a
    provider blip and re-create the overload. Randomising the wait spreads them out — this is the
    difference between recovering and amplifying an outage.
    """
    raw = min(_BACKOFF_BASE_SECONDS * (2**attempt), _BACKOFF_CAP_SECONDS)
    return raw / 2 + random.uniform(0, raw / 2)


def get_chat_model(
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    model: str | None = None,
) -> ChatGroq:
    """Build a Groq chat client.

    temperature stays low: this task is "answer from these documents and cite them", where creativity
    is hallucination. A higher temperature makes the model paraphrase source text into claims the
    chunks do not support, which the critique agent would then rightly reject — wasting the call that
    got it there.

    Streaming is enabled because the SSE chat path needs token deltas; without it langchain would
    buffer the full completion and the user would watch a spinner for the entire answer.
    """
    return ChatGroq(
        api_key=settings.groq_api_key,
        # A small, fast Llama-class model by default: the free tier's per-day token budget stretches
        # much further, and four agents per question need that headroom.
        model_name=model or "llama-3.1-8b-instant",
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=True,
    )


def stream_text(
    messages: list[BaseMessage],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    model: str | None = None,
) -> Iterator[str]:
    """Stream answer text, token by token, with rate-limit retries around the open.

    Only establishing the stream is retried. Once tokens are already reaching the user, restarting the
    call would append a second answer mid-sentence — so a mid-stream failure is raised and the router
    turns it into an SSE error event instead of silently duplicating output.
    """
    chat = get_chat_model(temperature=temperature, max_tokens=max_tokens, model=model)

    stream: Any = None
    last_error: BaseException | None = None
    for attempt in range(_MAX_ATTEMPTS):
        # Set once the provider has handed us a chunk to forward. From that moment a retry would re-open
        # the generation and append a second answer to the words already on the user's screen, so the
        # except branch below raises instead of looping.
        tokens_sent = False
        try:
            stream = chat.stream(messages)
            # Force the first chunk now so an immediate 429 is still inside the retry window.
            iterator = iter(stream)
            first = next(iterator, None)
            if first is None:
                return
            tokens_sent = True
            yield from _deltas(first)
            for chunk in iterator:
                yield from _deltas(chunk)
            return
        except Exception as exc:
            last_error = exc
            if tokens_sent:
                logger.error("llm stream failed after answering began: %s", exc)
                raise LLMUnavailableError(f"Model stream ended mid-answer: {exc}") from exc
            if not _retryable(exc):
                logger.error("llm failed non-retryably: %s", exc)
                raise LLMUnavailableError(f"Model call failed: {exc}") from exc
            if attempt == _MAX_ATTEMPTS - 1:
                break
            wait = _sleep_before(attempt)
            logger.warning(
                "llm rate-limited/transient (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                wait,
                exc,
            )
            time.sleep(wait)

    raise LLMUnavailableError(f"Model unavailable after {_MAX_ATTEMPTS} attempts: {last_error}")


def _deltas(chunk: Any) -> Iterator[str]:
    """Pull text out of an AIMessageChunk, skipping empty or non-text parts."""
    content = getattr(chunk, "content", None)
    if not content:
        return
    if isinstance(content, str):
        if content:
            yield content
        return
    # Some providers return a list of content blocks; only text blocks are streamable here.
    for block in content:
        if isinstance(block, str):
            yield block
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if text:
                yield text


def complete_text(
    messages: list[BaseMessage],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = 512,
    model: str | None = None,
) -> str:
    """Non-streaming completion, for the router and critique agents.

    Those agents produce a decision or a verdict, not prose for a human to watch, so buffering the
    whole response is simpler and cheaper than streaming it — and their output has to be parsed before
    the graph can branch on it anyway.
    """
    chat = get_chat_model(temperature=temperature, max_tokens=max_tokens, model=model)
    last_error: BaseException | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = chat.invoke(messages)
            text = response.content if isinstance(response.content, str) else str(response.content)
            return text.strip()
        except Exception as exc:
            last_error = exc
            if not _retryable(exc):
                raise LLMUnavailableError(f"Model call failed: {exc}") from exc
            if attempt == _MAX_ATTEMPTS - 1:
                break
            wait = _sleep_before(attempt)
            logger.warning(
                "llm completion retry %d/%d in %.1fs: %s", attempt + 1, _MAX_ATTEMPTS, wait, exc
            )
            time.sleep(wait)
    raise LLMUnavailableError(f"Model unavailable after {_MAX_ATTEMPTS} attempts: {last_error}")
