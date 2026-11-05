"""
routers/auth.py — POST /api/auth/session: exchange the demo code for a signed session token.

This is the only endpoint that accepts an unauthenticated request, and the only place a token is minted.
Everything else in the API resolves its owner id from that token (see core/deps.py), which is what closes
the hole the Phase 1-4 demo header left open: previously any caller could name any owner id in a header and
read that namespace's documents.

A failure here is logged, never echoed back with detail. "Wrong code" and "malformed key" are the only two
outcomes, and both say nothing about what the real value was.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import login_rate_limit
from app.core.security import (
    AuthenticationError,
    access_code_matches,
    mint_session,
    new_device_key,
    normalise_owner_id,
)
from app.models.auth import SessionRequest, SessionResponse

logger = logging.getLogger("documind.auth")

router = APIRouter()


@router.post("/auth/session", response_model=SessionResponse, tags=["auth"])
async def open_session(payload: SessionRequest, _: None = Depends(login_rate_limit)) -> SessionResponse:
    """Open a 24-hour session and return the token every other endpoint expects.

    A returning client sends its previous `owner_id` back as `device_key`; a new one gets one issued here
    and stores it alongside the token. Both paths return the same shape, so the frontend has one handler.
    """
    if not access_code_matches(payload.access_code):
        logger.warning("session refused: bad access code")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That access code is not valid for this demo.",
        )

    if payload.device_key:
        try:
            owner_id = normalise_owner_id(payload.device_key)
        except AuthenticationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        owner_id = new_device_key()

    token, expires_at = mint_session(owner_id)
    # The owner id is not sensitive (the client already has it) but recording it is what ties a later
    # "someone drained my quota" report to a specific namespace.
    logger.info("session issued owner_id=%s expires_at=%s", owner_id, expires_at.isoformat())
    return SessionResponse(
        access_token=token,
        owner_id=owner_id,
        expires_at=expires_at.isoformat(),
    )
