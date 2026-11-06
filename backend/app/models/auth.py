"""
models/auth.py — Request and response contracts for signing up and signing in.

`SessionResponse` kept the shape the access-code flow used: `owner_id` is the namespace the token unlocks,
an account's `user_id` is exactly that, and every router already reads it from the signed `sub`. Adding
accounts therefore changed who mints the token and not what the rest of the API accepts.

Passwords are `SecretStr`, not `str`. A plain field survives into a `repr()`, a debug log, an exception
traceback printed by a dependency, and a `model_dump()` that somebody adds later for a completely
different reason; SecretStr redacts all four. The cost is `.get_secret_value()` at the two places that
actually need the bytes.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, SecretStr

from app.core.password import MAX_LENGTH, MIN_LENGTH


class RegisterRequest(BaseModel):
    email: EmailStr = Field(description="Address to send the verification link to.")
    password: SecretStr = Field(
        ...,
        min_length=MIN_LENGTH,
        max_length=MAX_LENGTH,
        description=f"{MIN_LENGTH}-{MAX_LENGTH} characters, not a common one.",
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(..., max_length=MAX_LENGTH)


class ResendRequest(BaseModel):
    email: EmailStr = Field(description="The address to send a fresh verification link to.")


class VerifyRequest(BaseModel):
    # Bounded: the token is a JWT this service minted, so anything much longer than ~500 characters is
    # either a paste error or somebody testing how much the log will swallow.
    token: str = Field(..., min_length=16, max_length=2048)


class SessionResponse(BaseModel):
    access_token: str = Field(description="Send as 'Authorization: Bearer <token>' on every API call.")
    token_type: str = Field(default="Bearer")
    owner_id: str = Field(description="The namespace this token unlocks.")
    expires_at: str = Field(description="RFC 3339 timestamp; the client should sign in again before this.")


class VerifyResponse(BaseModel):
    email: str = Field(description="The address now confirmed.")


class AcceptedResponse(BaseModel):
    """The only thing register and resend are allowed to say.

    Identical body for "we made an account" and "that address already has one": a signup endpoint whose
    response varies with whether you are registered is a subscriber list, and this one is public.
    """

    message: str
