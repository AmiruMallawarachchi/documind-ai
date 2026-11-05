"""
services/user_store.py — Accounts: the only collection that holds a credential.

One row per person: a stable `user_id`, a lowercased email, an Argon2id hash, and whether the address has
been verified. `user_id` doubles as `owner_id` everywhere else in the app, which is why documents,
conversations and messages are already keyed by an opaque owner string — an account is a new kind of
owner, not a new data model.

Three rules this module holds to:

  - The plaintext password never comes near it. Callers hash through `app.core.password` and pass the
    hash in, so a stray log of a Mongo write cannot leak a secret.
  - The email is lowercased before it is used as a key. `Amiru@Gmail.com` and `amiru@gmail.com` are the
    same mailbox to a mail server, and a unique index treats them as two rows unless they are normalised
    first.
  - `user_id` is a random hex, not a Mongo ObjectId. Account ids get embedded in verification links, so
    a sequential one would make "this address registered, and the next one is too" guessable.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pymongo.errors import DuplicateKeyError

# The shared client, not a second one: a per-service MongoClient would open its own monitor threads and
# double the connection count against Atlas M0's small pool. Index creation happens once, in
# metadata_store._ensure_indexes, because that is the single cold-start path for this database.
from app.services.metadata_store import _USERS, _database, _utc_now

logger = logging.getLogger(__name__)

MAX_EMAIL_LENGTH = 254  # RFC 5321 limit on the reverse-path; longer than this cannot be delivered to.


class AccountExistsError(Exception):
    """That email already has an account. Routers answer 202, not 409 — see `create_account`."""


def normalize_email(raw: str) -> str:
    """Trim and lowercase an address for use as a lookup key.

    Public because the router must normalise the login lookup with the same function that normalised the
    signup insert; two slightly different normalisers would let an account register on one spelling and
    never find it on the other.
    """
    cleaned = raw.strip().lower()
    if len(cleaned) > MAX_EMAIL_LENGTH:
        raise ValueError(f"Email addresses longer than {MAX_EMAIL_LENGTH} characters cannot receive mail.")
    return cleaned


def new_user_id() -> str:
    """A non-sequential account id.

    32 hex characters, comfortably inside the 128-character ceiling the owner-id path applies before a
    value reaches a Mongo filter or a Pinecone metadata field.
    """
    return uuid.uuid4().hex


def create_account(email: str, password_hash: str) -> dict[str, Any]:
    """Insert an unverified account and return the row.

    Raises `AccountExistsError` on a duplicate email. The caller still answers "check your inbox": the
    unique index is the only place a second signup for an address can be detected, so distinguishing
    "created" from "already existed" at this layer is what would leak who has an account.
    """
    record = {
        "user_id": new_user_id(),
        "email": normalize_email(email),
        "password_hash": password_hash,
        # False until the verification link is clicked. Nothing in the app checks this except the login
        # path, which is the point: an unverified address cannot open a session.
        "email_verified": False,
        "created_at": _utc_now(),
        "verified_at": None,
    }
    try:
        _database()[_USERS].insert_one(record)
    except DuplicateKeyError as exc:
        logger.info("signup refused: email already registered")
        raise AccountExistsError(record["email"]) from exc
    return record


def find_by_email(email: str) -> dict[str, Any] | None:
    """Look an account up by address.

    The email index is unique, so this is an index hit rather than a scan. `_id` is dropped by
    projection: this row is the one place a raw Mongo document is close to the response, and a router
    that later dumps the whole dict should not be able to leak the internal id.
    """
    return _database()[_USERS].find_one({"email": normalize_email(email)}, {"_id": 0})


def find_by_id(user_id: str) -> dict[str, Any] | None:
    return _database()[_USERS].find_one({"user_id": user_id}, {"_id": 0})


def mark_verified(user_id: str) -> bool:
    """Flag an account's address as verified. Idempotent: re-clicking a link returns True either way.

    Scoped on both keys so a token for one account cannot flip another's flag even if the id and the
    verification decision are ever assembled from different sources.
    """
    result = _database()[_USERS].update_one(
        {"user_id": user_id, "email_verified": False},
        {"$set": {"email_verified": True, "verified_at": _utc_now()}},
    )
    if result.matched_count:
        return True
    return find_by_id(user_id) is not None


def store_password_hash(user_id: str, password_hash: str) -> None:
    """Replace an account's hash.

    The only intended caller is the login path, on the one occasion the plaintext is available and the old
    hash has just verified: a cost increase in `core/password.py` then reaches existing accounts as they
    sign in, which is the only way to move them without asking anyone to change a working password.
    """
    _database()[_USERS].update_one(
        {"user_id": user_id},
        {"$set": {"password_hash": password_hash}},
    )


def count_accounts() -> int:
    """How many accounts exist — for /api/health/deep and for a demo seed that must stay idempotent."""
    return _database()[_USERS].count_documents({})
