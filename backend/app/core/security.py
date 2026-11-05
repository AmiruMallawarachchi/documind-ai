"""
core/security.py — Tokens: proving who a caller is from a signature, not from a header.

Two token kinds share this module and one secret. A session token's `sub` is the owner id every other
endpoint scopes its queries by; a verification token's `sub` is an account id and its `purpose` says what
it is for. They are kept apart on purpose — see PURPOSE_VERIFY.

The visitor path is mid-migration. `access_code_matches` and the device-key helpers serve the published
demo code, which predates accounts; `mint_session` is the seam both paths share, so switching the frontend
to email and password changes who mints the token and not how every router reads it.

Two properties matter and they are different:
  - The access code gates *whether* a caller may use the API at all. It is published in the README, so it
    is a toll booth, not a secret.
  - The signature gates *whose* namespace a caller gets. Someone who guesses that another visitor's owner
    id still cannot mint a token for it, which is exactly what the old X-Demo-Key header allowed.

HS256 is symmetric on purpose: one shared secret, no key distribution.
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

# A verification link and a session are both signed with the same key and both carry a `sub`. The
# `purpose` claim is the only thing that keeps them apart, and without it a link emailed to a visitor
# would double as a token that unlocks their documents — which is a much worse leak than an expired
# session, because the link is delivered over a channel the account holder does not control.
PURPOSE_VERIFY = "verify"


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


def _signed(claims: dict[str, Any]) -> str:
    """Sign a claim set and hand back a str, never bytes.

    PyJWT 2 returns str; 1.x returned bytes. Decoding defensively here is what keeps a dependency bump
    from putting a b"..." prefix into an Authorization header or a query string.
    """
    token = jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)
    return token.decode("utf-8") if isinstance(token, bytes) else token


def _window(minutes: int) -> tuple[int, int]:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=minutes)
    return int(issued_at.timestamp()), int(expires_at.timestamp())


def mint_session(owner_id: str) -> tuple[str, datetime]:
    """Sign a token for one owner and return it with its expiry.

    No `purpose` claim: this is the shape every router already accepts, and leaving it unchanged is what
    lets accounts arrive without touching a single query filter.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_ttl_minutes)
    issued, expiry = _window(settings.jwt_ttl_minutes)
    return _signed({"sub": owner_id, "iat": issued, "exp": expiry}), expires_at


def mint_verification(user_id: str) -> tuple[str, datetime]:
    """Sign a one-purpose token for the address that just registered.

    Its lifetime is separate from the session's (`VERIFICATION_TTL_MINUTES`) because the two fail
    differently: a session that outlives its usefulness is a stolen-cookie risk, while a verification link
    that expires before the mail is read is a visitor who cannot get into their own account.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.verification_ttl_minutes)
    issued, expiry = _window(settings.verification_ttl_minutes)
    return _signed({"sub": user_id, "purpose": PURPOSE_VERIFY, "iat": issued, "exp": expiry}), expires_at


def _claims_from(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        # Wording is shared by both token kinds because `_claims_from` is, and neither caller can tell a
        # stale session from a stale link before it gets here. What matters is that both say "expired".
        raise AuthenticationError("That has expired; please get a new one.") from exc
    except jwt.InvalidTokenError as exc:
        # Deliberately generic on purpose: "wrong signature" versus "malformed base64" tells an attacker
        # nothing useful and tells a legitimate user nothing either.
        raise AuthenticationError("The token is not valid.") from exc


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
    if claims.get("purpose") is not None:
        # A verification link travels over email, which is the least controlled channel here: it gets
        # forwarded, opened by a mailing-list archive, and cached by a webmail proxy. It must not be a
        # session, even though it is signed by the same key and carries the same subject.
        raise AuthenticationError("That token is not a session.")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthenticationError("The session token carries no identity.")
    return normalise_owner_id(subject)


def user_id_from_verification(token: str) -> str:
    """Resolve the account id a verification link was issued for, or refuse it.

    `purpose` is checked rather than merely present: a token with no purpose is a session token, and a
    session token pasted into /auth/verify would otherwise confirm an address without anyone proving they
    read the mail.
    """
    claims = _claims_from(token.strip())
    if claims.get("purpose") != PURPOSE_VERIFY:
        raise AuthenticationError("That link is not a verification link.")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthenticationError("That link carries no account.")
    return normalise_owner_id(subject)
