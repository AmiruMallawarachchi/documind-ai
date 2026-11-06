"""
routers/auth.py — Who may open a session, and how they prove an address is theirs.

Everything a caller does after this module is authorised by the signed token minted here (see
core/deps.py), so the shape of `SessionResponse` is the contract: an account's `user_id` becomes the
`owner_id` that every Mongo filter and Pinecone namespace already keys on.

Three rules the four endpoints share:

  - Same answer for every outcome that could reveal an account. Register returns 202 whether the address
    was new or already taken, and login answers 401 "wrong email or password" whether the address is
    unknown or the password is. A signup form that says "that email is already registered" is a
    subscriber list, and this one is public.
  - The email is normalised by the same function that normalises the stored one.
  - These are sync `def` endpoints, deliberately: each one does a blocking Mongo write and, on the two
    that send, a handshake with an SMTP server that is allowed ten seconds. FastAPI runs a sync endpoint
    on a worker thread and an `async def` one on the event loop, and a `def`-typed mistake here would
    stall every other in-flight request — including the SSE chat stream — for the duration of a send.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import mail, password
from app.core.config import settings
from app.core.deps import login_rate_limit, signup_rate_limit
from app.core.security import (
    AuthenticationError,
    mint_session,
    mint_verification,
    user_id_from_verification,
)
from app.models.auth import (
    AcceptedResponse,
    LoginRequest,
    RegisterRequest,
    ResendRequest,
    SessionResponse,
    VerifyRequest,
    VerifyResponse,
)
from app.services import user_store

logger = logging.getLogger("documind.auth")

router = APIRouter()

CHECK_YOUR_INBOX = "A confirmation link is on its way to that address. It expires in a day."
BAD_CREDENTIALS = "Wrong email or password."


def _send_link(record: dict) -> None:
    """Mint a verification token for one account and post the link to its address."""
    token, _expires_at = mint_verification(record["user_id"])
    link = f"{settings.frontend_origin}/verify?token={token}"
    try:
        mail.send_verification_link(record["email"], link)
    except mail.MailError as exc:
        # 502, not 202. Telling a visitor "check your inbox" when nothing left this process is how they
        # sit there refreshing a page; the account row still exists, so /auth/resend can retry.
        logger.error("verification mail refused: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We could not send the confirmation email. Try again in a moment.",
        ) from exc


@router.post("/auth/register", response_model=AcceptedResponse, status_code=202, tags=["auth"])
def register(payload: RegisterRequest, _: None = Depends(signup_rate_limit)) -> AcceptedResponse:
    """Create an unverified account and send its confirmation link.

    Both branches answer the same way and both send a mail, so the response and its timing say nothing
    about whether the address was already registered.
    """
    email = user_store.normalize_email(str(payload.email))
    plain = payload.password.get_secret_value()

    try:
        record = user_store.create_account(email, password.create_hash(plain, email=email))
    except password.PasswordPolicyError as exc:
        # Reached when a password clears the schema's length bounds but not the common-password or
        # reuse-your-address rules, which live in the hashing module on purpose.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except user_store.AccountExistsError:
        # Never touch the existing row. A second signup from someone else for a known address must not be
        # able to reset its password, un-verify it, or otherwise disturb it — it gets a fresh link sent
        # and nothing more.
        record = user_store.find_by_email(email)

    _send_link(record)
    logger.info("signup accepted email=%s", email)
    return AcceptedResponse(message=CHECK_YOUR_INBOX)


@router.post("/auth/resend", response_model=AcceptedResponse, status_code=202, tags=["auth"])
def resend_verification(payload: ResendRequest, _: None = Depends(signup_rate_limit)) -> AcceptedResponse:
    """Send a new link to an address that has not confirmed yet.

    The honest limit of the no-enumeration design: a known-and-verified address returns instantly while
    an unknown one does too, but a known-and-unverified one pays an SMTP round trip. The hourly signup
    bucket is what bounds how much of that difference a caller can measure.
    """
    record = user_store.find_by_email(user_store.normalize_email(str(payload.email)))
    if record is not None and not record.get("email_verified"):
        _send_link(record)
    return AcceptedResponse(message=CHECK_YOUR_INBOX)


@router.post("/auth/verify", response_model=VerifyResponse, tags=["auth"])
def verify_email(payload: VerifyRequest, _: None = Depends(login_rate_limit)) -> VerifyResponse:
    """Turn a verification token into a confirmed address."""
    try:
        user_id = user_id_from_verification(payload.token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not user_store.mark_verified(user_id):
        # Signed correctly but no such account: the link was minted for an address that is no longer in
        # the database. Same 400 wording as any other bad link, so this stays uninteresting to probe.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That confirmation link is not valid. Request a new one.",
        )

    record = user_store.find_by_id(user_id)
    logger.info("email confirmed email=%s", record["email"] if record else "unknown")
    return VerifyResponse(email=record["email"] if record else "")


@router.post("/auth/login", response_model=SessionResponse, tags=["auth"])
def login(payload: LoginRequest, _: None = Depends(login_rate_limit)) -> SessionResponse:
    """Exchange an email and password for a session token.

    The password is checked before the verification flag, and the flag's failure is the one answer that
    differs from the generic 401. That is a deliberate trade: telling a visitor "confirm your email" is
    the difference between a working signup and a support ticket, and it only speaks about accounts that
    never finished signing up.
    """
    email = user_store.normalize_email(str(payload.email))
    plain = payload.password.get_secret_value()
    record = user_store.find_by_email(email)

    if record is None:
        # Same work as a real verification, so the response time does not announce that this address has
        # no account. See core/password.blind_verify.
        password.blind_verify()
        logger.warning("login refused email=%s reason=no-such-account", email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=BAD_CREDENTIALS)

    if not password.verify(plain, record["password_hash"]):
        logger.warning("login refused email=%s reason=bad-password", email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=BAD_CREDENTIALS)

    if not record.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Confirm your email address first — we sent a link when you signed up.",
        )

    if password.needs_rehash(record["password_hash"]):
        # This is the only moment the plaintext exists, so it is the only moment an old, weaker hash can
        # be upgraded. Silent, and the account holder cannot tell it happened.
        user_store.store_password_hash(record["user_id"], password.create_hash(plain, email=email))
        logger.info("password rehashed at raised cost email=%s", email)

    token, expires_at = mint_session(record["user_id"])
    logger.info("login ok email=%s owner_id=%s", email, record["user_id"])
    return SessionResponse(
        access_token=token,
        owner_id=record["user_id"],
        expires_at=expires_at.isoformat(),
    )
