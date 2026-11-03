"""
core/deps.py — Shared FastAPI dependencies: who is calling, and whether they may spend quota doing it.

Dependencies are how FastAPI injects cross-cutting concerns without every endpoint re-implementing them.
Two rules live here, and both are the kind that get forgotten per-route if they are not central: identity
(one function, so a router cannot accidentally skip it) and the per-owner rate limit (same reason).

The rate dependencies deliberately depend on `current_owner_id` themselves rather than taking the owner as
an argument: FastAPI caches a resolved sub-dependency per request, so a route can declare both the owner it
wants to use and the limit it must respect and the header is parsed exactly once.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import settings
from app.core.rate_limit import client_address, limiter, reject
from app.core.security import AuthenticationError, owner_from_authorization


async def current_owner_id(
    authorization: str | None = Header(default=None),
    x_demo_key: str | None = Header(default=None),
) -> str:
    """Resolve the requesting user's owner id.

    Production traffic authenticates with a session JWT from POST /api/auth/session, and the owner id is
    read from its signed `sub` claim — a client cannot claim someone else's namespace without forging the
    signature. The `X-Demo-Key` header still works outside production so that local runs, tests, and the
    offline server can stand up two namespaces and prove isolation without signing tokens by hand.

    Keeping that fallback gated on the environment matters: the query-level `owner_id` filter in Pinecone
    and Mongo is only as good as the identity behind it, and a deployed instance must never accept an
    unverified one.
    """
    try:
        owner = owner_from_authorization(authorization)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if owner is not None:
        return owner

    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Send 'Authorization: Bearer <session token>'; get one from POST /api/auth/session.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if x_demo_key is not None:
        cleaned = x_demo_key.strip()
        if not cleaned:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Demo-Key was sent but empty; omit the header to use the default user.",
            )
        if len(cleaned) > 128:
            # Bounds the value before it reaches a Mongo filter or Pinecone metadata field, where an
            # oversized string would surface as an opaque upstream 400.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Demo-Key must be 128 characters or fewer.",
            )
        return cleaned

    return settings.default_owner_id


def chat_rate_limit(owner_id: str = Depends(current_owner_id)) -> None:
    """Cap answer requests per owner: this is the route that spends Groq tokens."""
    retry_after = limiter.check("chat", owner_id, settings.chat_requests_per_minute)
    if retry_after:
        reject("chat requests per minute", retry_after)


def upload_rate_limit(owner_id: str = Depends(current_owner_id)) -> None:
    """Cap uploads per owner: each one costs an embedding pass over the whole file."""
    retry_after = limiter.check("upload", owner_id, settings.upload_requests_per_minute)
    if retry_after:
        reject("upload requests per minute", retry_after)


def login_rate_limit(request: Request) -> None:
    """Cap session attempts per address — the only limit that can apply before an identity exists.

    The code is published, so this is not really about guessing it; it is about stopping one machine from
    minting thousands of owner namespaces, each of which would carry its own budget.
    """
    retry_after = limiter.check("login", client_address(request), settings.login_requests_per_minute)
    if retry_after:
        reject("session attempts per minute", retry_after)
