"""
models/auth.py — Request and response contracts for the session endpoint.

`device_key` is the stable identity a returning visitor already has; sending it back on re-login keeps
their documents and conversations attached to the same owner id. Omitting it means the server mints a new
namespace, which is what a first visit should do.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionRequest(BaseModel):
    access_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="The demo code from the README. Required once per session, not per request.",
    )
    device_key: str | None = Field(
        default=None,
        max_length=128,
        description="The owner id from a previous session, so a returning visitor sees their own files.",
    )


class SessionResponse(BaseModel):
    access_token: str = Field(description="Send as 'Authorization: Bearer <token>' on every API call.")
    token_type: str = Field(default="Bearer")
    owner_id: str = Field(description="The namespace this token unlocks. Store it and resend it as device_key.")
    expires_at: str = Field(description="RFC 3339 timestamp; the client should re-login before this.")
