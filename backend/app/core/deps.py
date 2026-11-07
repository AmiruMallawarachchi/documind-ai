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

    Production traffic authenticates with a session JWT from POST /api/auth/login, and the owner id is read
    from its signed `sub` claim — a client cannot claim someone else's namespace without forging the
    signature and knowing the account's password. The `X-Demo-Key` header still works outside production so
    that local runs, tests, and the offline server can stand up two namespaces and prove isolation without
    signing tokens by hand.

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
            detail="Send 'Authorization: Bearer <session token>'; get one from POST /api/auth/login.",
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
    """Cap sign-in and verification attempts per address — the limit that can apply before an identity
    exists.

    Login is what an attacker uses to test a stolen credential list, and each refusal here is an Argon2
    verification the server pays for. The per-address bucket is the only handle available: until a session
    is minted there is no owner id to key on, and the address comes from `X-Forwarded-For`.
    """
    retry_after = limiter.check("login", client_address(request), settings.login_requests_per_minute)
    if retry_after:
        reject("sign-in attempts per minute", retry_after)


def shared_rate_limit(request: Request) -> None:
    """Cap reads of a shared conversation per client address.

    This is the one route with no identity to key on: it is deliberately public, so the address from
    `X-Forwarded-For` is the only handle. The token is 128 bits and cannot be guessed at any useful rate,
    but the limit is what stops one visitor turning a link they already hold into a scrape of the free
    instance's CPU and Atlas reads.
    """
    retry_after = limiter.check(
        "shared", client_address(request), settings.shared_requests_per_minute
    )
    if retry_after:
        reject("shared-conversation reads per minute", retry_after)


def signup_rate_limit(request: Request) -> None:
    """Cap account creation and link resending per address, over an hour.

    The budget this protects is not CPU, it is two things that accumulate: rows in a 512 MB Atlas cluster
    and mails sent through a Gmail account that will rate-limit or lock the sender if it is used as a
    relay for strangers. A five-per-minute bucket would let a script open hundreds of accounts an hour.
    """
    retry_after = limiter.check(
        "signup", client_address(request), settings.signup_requests_per_hour, window_seconds=3600
    )
    if retry_after:
        reject("sign-up attempts per hour", retry_after)
