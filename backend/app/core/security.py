"""
core/security.py — Session tokens: proving who a caller is without a user database.

The demo has no accounts table, which is the honest reason a "code + signed token" scheme is enough. A
visitor types the published access code once, this module issues an HS256 JWT whose `sub` claim is their
owner id, and every later request is authorised by verifying that signature rather than by trusting a
header the client invented.

Two properties matter and they are different:
  - The access code gates *whether* a caller may use the API at all. It is published in the README, so it
    is a toll booth, not a secret.
  - The signature gates *whose* namespace a caller gets. Someone who guesses that another visitor's owner
    id still cannot mint a token for it, which is exactly what the old X-Demo-Key header allowed.

HS256 is symmetric on purpose: one shared secret, no key distribution. If this ever grows real user
accounts, replace the code check with a password/identity provider and keep everything below — the token
shape, `owner_from_authorization`, and the routers that call it — unchanged.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import settings

ALGORITHM = "HS256"
TOKEN_TYPE = "Bearer"

# Same ceiling the demo-key path always applied: the owner id goes into a Mongo filter and Pinecone
# metadata, and an unbounded string there fails as an opaque upstream 400 instead of a clean 401.
MAX_OWNER_ID_LENGTH = 128


class AuthenticationError(Exception):
    """The caller produced no usable token. Routers turn this into a 401."""


def new_device_key() -> str:
    """A fresh owner id for a first-time visitor, handed back so the client can store it."""
    return uuid.uuid4().hex


def normalise_owner_id(candidate: str) -> str:
    """Trim and bound a client-supplied device key, or raise if it is unusable."""
    cleaned = candidate.strip()
    if not cleaned:
        raise AuthenticationError("device_key was sent but empty.")
    if len(cleaned) > MAX_OWNER_ID_LENGTH:
        raise AuthenticationError(f"device_key must be {MAX_OWNER_ID_LENGTH} characters or fewer.")
    return cleaned


def access_code_matches(candidate: str) -> bool:
    """Constant-time compare, so the length and prefix of the real code are not observable in timing."""
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), settings.demo_access_code.encode("utf-8"))


def mint_session(owner_id: str) -> tuple[str, datetime]:
    """Sign a token for one owner and return it with its expiry."""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_ttl_minutes)
    issued_at = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": owner_id, "iat": int(issued_at.timestamp()), "exp": int(expires_at.timestamp())},
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    # PyJWT 2 returns str; 1.x returned bytes. Decoding defensively here means a dependency bump cannot
    # put a b"..." prefix into an Authorization header.
    return (token.decode("utf-8") if isinstance(token, bytes) else token), expires_at


def _claims_from(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("This session has expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        # Deliberately generic on purpose: "wrong signature" versus "malformed base64" tells an attacker
        # nothing useful and tells a legitimate user nothing either.
        raise AuthenticationError("The session token is not valid.") from exc


def owner_from_authorization(authorization: str | None) -> str | None:
    """Resolve an owner id from an `Authorization: Bearer <jwt>` header.

    Returns None when the header is absent, so the caller can decide between "unauthenticated" (401) and
    "fall back to demo mode" — the two are different decisions and belong in the dependency, not here.
    """
    if authorization is None:
        return None
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise AuthenticationError("Send 'Authorization: Bearer <session token>'.")

    claims = _claims_from(credential.strip())
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthenticationError("The session token carries no identity.")
    return normalise_owner_id(subject)
