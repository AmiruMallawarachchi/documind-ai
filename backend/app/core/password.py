"""
core/password.py — Argon2id hashing and the floor the policy will accept.

Argon2id, not bcrypt or SHA-256. A leaked hash table is only as expensive to crack as the slowest hash
in it, and Argon2id is memory-hard: an attacker cannot amortise a GPU or an ASIC across many guesses the
way they can with bcrypt. It is also the current OWASP recommendation, which makes it the defensible
choice to name in a write-up.

Costs are set for the machine this actually runs on. Render's free instance has 512 MB of RAM and the
web workers are CPU-limited, so verification has to stay inside one worker's budget while still costing
an attacker real memory per guess.

The policy is enforced here rather than in a Pydantic field so that it cannot be routed around: hashing
is this module's job, so a plaintext password reaching `create_hash` from anywhere still meets the same
floor.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

MIN_LENGTH = 12
# Bounded before hashing so a request body cannot hand the hasher an arbitrarily long string; 512 is far
# past any password a person will type and short enough to keep inside a body-size limit.
MAX_LENGTH = 512

# OWASP's Argon2id baseline for a memory-constrained server: ~19 MiB per hash, so a worker verifying a
# login never approaches Render's 512 MB ceiling while an attacker still pays for memory per guess.
_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1, hash_len=32, salt_len=16)


class PasswordPolicyError(Exception):
    """The password is not acceptable. Routers turn this into a 422 with the message as detail."""


# A small denylist beats a length rule alone: "correcthorsebatterystaple" is 25 characters and instantly
# cracked. These are the bases every public breach wordlist tries first, checked with and without
# surrounding digits so "password123" and "1monkey1" are both refused. Deliberately short — a real
# deployment would check against a corpus of millions of leaked hashes, which is not something to ship
# inside a 512 MB container.
COMMON_PASSWORDS = frozenset(
    {
        "password",
        "passwort",
        "qwerty",
        "letmein",
        "iloveyou",
        "welcome",
        "sunshine",
        "princess",
        "monkey",
        "trustno",
        "football",
        "baseball",
        "superman",
        "batman",
        "admin",
        "abc",
        # Pure digit runs have no letters to strip down to a base, so they are listed whole.
        "123456789012",
        "123456789012345",
        "111111111111",
        "000000000000",
    }
)

# Checked against when the signer is also the account holder, so "you@you.com" / "you@you.com" cannot
# pass a length-and-denylist check.
_MIN_DISTINCT_CHARACTERS = 4
# How long an email local part has to be before reusing it in a password is a meaningful signal rather
# than a penalty for having a short address.
_MIN_ADDRESS_FRAGMENT = 6


def policy_error(password: str, *, email: str | None = None) -> str | None:
    """Return a human-readable reason the password is unusable, or None when it is acceptable."""
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use no more than {MAX_LENGTH} characters."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or lowered.strip("0123456789") in COMMON_PASSWORDS:
        return "That password is too common to be safe. Choose something else."
    if len(set(lowered)) < _MIN_DISTINCT_CHARACTERS:
        return "That password repeats too few characters to be safe."
    if email:
        local_part, _, _ = email.partition("@")
        # Both directions. "amirunoel8" inside a longer password is the same mistake as a password that is
        # a truncation of the address, and the local part is the one half of the address an attacker is
        # certain to know — it is in the From line of every mail the account holder has ever sent.
        # Only for a local part with some length to it: `a@b.com` would otherwise refuse every password
        # containing the letter a, which punishes the user for the shape of their address.
        if len(local_part) >= _MIN_ADDRESS_FRAGMENT and (local_part in lowered or lowered in local_part):
            return "Do not reuse part of your email address in your password."
    return None


def create_hash(password: str, *, email: str | None = None) -> str:
    """Hash a new password, refusing one that does not meet the policy.

    The check lives here so `users.password_hash` can only ever hold a hash of a policy-meeting
    password, whichever caller asked for it. `email` is passed through rather than checked by the caller
    so the "do not reuse your address" rule cannot be left out of a code path.
    """
    reason = policy_error(password, email=email)
    if reason is not None:
        raise PasswordPolicyError(reason)
    # Argon2 embeds a fresh random salt in the output, so two accounts with the same password produce
    # different hashes — which is what stops a hash table from being read as "these users share a secret".
    return _hasher.hash(password.encode("utf-8"))


def verify(password: str, hashed: str | None) -> bool:
    """Check a candidate password against a stored hash; never raises.

    A malformed, truncated, or foreign-format hash is treated as a failed login rather than a 500: the
    stored value is data, and bad data in a row must not turn an attempted sign-in into a server error.
    """
    if not hashed or not password:
        return False
    try:
        return _hasher.verify(hashed, password.encode("utf-8"))
    except (Argon2Error, InvalidHashError):
        # Two trees, not one: argon2-cffi's VerifyMismatchError sits under Argon2Error, but a hash it
        # cannot even parse raises InvalidHashError — a ValueError. Catching only the Argon2 tree turns a
        # bcrypt-shaped row into a 500 on the login path, which is a free way to probe the database.
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash used weaker parameters than this module now applies.

    Raised cost is the only way to move accounts off an old setting. Rehashing on successful login is
    the standard upgrade path, and this is the predicate that decides it. An unparseable hash counts as
    needing a rehash: the login that reaches it will fail anyway, and a caller that re-derives the hash
    on success repairs the row.
    """
    try:
        return _hasher.check_needs_rehash(hashed)
    except (Argon2Error, InvalidHashError):
        return True


# Hashed once at import so the no-account branch of a login costs the same as the has-account branch.
# `secrets` rather than a literal: the value is discarded, but a hardcoded string in a security module
# invites someone later to treat it as a real credential.
_DUMMY_HASH = create_hash(secrets.token_hex(32))


def blind_verify() -> None:
    """Spend a real verification's worth of time on a hash nobody can match.

    Without this, "unknown email" answers in ~1 ms and "wrong password" in ~60 ms, and the difference is
    a free oracle for enumerating which addresses have accounts. Callers use it on the no-such-user path
    and then return the same generic failure they return for a wrong password.
    """
    verify(secrets.token_hex(16), _DUMMY_HASH)
