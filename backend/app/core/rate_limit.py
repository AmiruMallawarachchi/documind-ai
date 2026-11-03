"""
core/rate_limit.py — Sliding-window limits per owner, held in this process's memory.

Why a 60-line module instead of `slowapi`: its default storage is a dict in the same process, so the
behaviour would be identical while adding two pinned dependencies and a second way of expressing a rule
we state in three places. The trade-off is real and worth naming — these counters are per worker process,
so running N workers multiplies every limit by N. Render's free tier runs one uvicorn process, and if that
changes the fix is a shared store (Redis), not a different library.

The window is a deque of timestamps rather than a fixed-window counter: a counter that resets on the
minute lets a client fire the whole budget at :59 and again at :01, which is 2x the intended rate through
the door. Keeping the last 60 seconds of hits makes the limit mean what it says.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status


class _Window:
    __slots__ = ("hits",)

    def __init__(self) -> None:
        self.hits: Deque[float] = deque()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<_Window hits={len(self.hits)}>"


class SlidingWindowLimiter:
    """Allow at most `limit` hits per `key` per `window_seconds`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: Dict[str, _Window] = {}

    def check(self, bucket: str, key: str, limit: int, window_seconds: int = 60) -> float:
        """Return 0.0 when allowed, or the seconds to wait when not.

        The caller raises the HTTP error; keeping this method total (it never raises) is what makes it
        directly testable without a request in flight.
        """
        if limit <= 0:
            return 0.0
        now = time.monotonic()
        oldest = now - window_seconds
        namespaced = f"{bucket}:{key}"

        with self._lock:
            window = self._windows.setdefault(namespaced, _Window())
            while window.hits and window.hits[0] <= oldest:
                window.hits.popleft()
            if len(window.hits) >= limit:
                # The wait is when the oldest hit currently in the window falls out of it.
                return max(1.0, window.hits[0] + window_seconds - now)
            window.hits.append(now)
            return 0.0

    def reset(self) -> None:
        """Forget every window. Used by tests, and by nothing in production."""
        with self._lock:
            self._windows.clear()

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._windows)


limiter = SlidingWindowLimiter()


def client_address(request: Request) -> str:
    """Best available source address for a pre-login caller.

    Behind Render's proxy `request.client.host` is the proxy, so the leftmost X-Forwarded-For entry is the
    visitor. That header is client-controlled in general — here it only selects a rate-limit bucket, and
    the authenticated limits key off the signed owner id instead.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:128] or "unknown"
    return request.client.host if request.client else "unknown"


def reject(bucket: str, retry_after: float) -> None:
    """Raise the 429.

    Kept out of the limiter class so `check` stays total — it returns a number and never touches HTTP —
    which is what lets the unit tests assert the window maths directly, without a request in flight.
    """
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"That is more than this demo allows for the moment ({bucket}). Try again in {int(retry_after)}s.",
        headers={"Retry-After": str(int(retry_after))},
    )
