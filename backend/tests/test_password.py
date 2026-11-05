"""
tests/test_password.py — Argon2id hashing, and the policy that gates it.

These tests are slow on purpose: a real Argon2 verification costs ~20 MiB and tens of milliseconds, which
is the whole point of the scheme and exactly why the KDF is called for real here instead of being mocked.
A fake hasher would prove the code calls a function; it could not prove the parameters are strong enough.

The assertions track the three promises the module makes: a hash is salted and self-describing, a wrong
password cannot be distinguished from a missing account by timing, and a weak password cannot reach the
database whichever router tried to store it.
"""

from __future__ import annotations

import re

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError

from app.core import password

GOOD = "herons-quietly-fold-6"


# ── the hash itself ────────────────────────────────────────────────────────────


def test_a_password_verifies_against_its_own_hash():
    assert password.verify(GOOD, password.create_hash(GOOD)) is True


def test_a_different_password_does_not_verify():
    assert password.verify(GOOD + "x", password.create_hash(GOOD)) is False


def test_two_accounts_with_the_same_password_get_different_hashes():
    """Argon2 stores a random salt inside the output. Without that, two users sharing a secret would be
    obvious from the hash column and one crack would unlock both."""
    assert password.create_hash(GOOD) != password.create_hash(GOOD)


def test_the_hash_records_the_parameters_it_was_made_with():
    """A self-describing hash is what lets costs be raised later without guessing per row. The `id`
    prefix is Argon2id specifically; Argon2i or Argon2d would be a different, weaker algorithm.

    The cost string is read back against the hasher's own settings rather than written as a literal
    twice: if the two ever disagreed, the test would be describing a hash the code no longer makes."""
    algorithm, version, parameters, _salt, _digest = password.create_hash(GOOD).split("$")[1:]

    assert algorithm == "argon2id"
    assert version == "v=19"
    assert parameters == f"m={password._hasher.memory_cost},t={password._hasher.time_cost},p=1"


def test_the_cost_is_the_owasp_floor_and_still_inside_the_containers_memory():
    """19 MiB at t=2, p=1 is OWASP's Argon2id baseline for a memory-constrained server. Below it the hash
    is no longer meaningfully memory-hard; above it, Render's 512 MB free instance and its single shared
    CPU turn a login storm into an OOM restart. Pinned as numbers, not compared to the constructor, so a
    careless edit to the parameters has to fail here deliberately."""
    assert (password._hasher.memory_cost, password._hasher.time_cost, password._hasher.parallelism) == (19_456, 2, 1)
    assert password._hasher.memory_cost * 1024 < 32 * 1024 * 1024


# ── policy ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        ("", "at least"),
        ("short123", "at least"),
        ("a" * (password.MAX_LENGTH + 1), "no more than"),
        ("Password1234", "too common"),
        ("LETMEIN12345", "too common"),
        ("000000000000", "too common"),
        ("aaaaaaaaaaaa", "too few characters"),
        ("121212121212", "too few characters"),
    ],
)
def test_the_policy_refuses_each_weak_shape_for_its_own_reason(candidate, reason):
    error = password.policy_error(candidate)

    assert error is not None
    assert reason in error


def test_a_digitsuffix_of_a_breach_list_password_is_still_refused():
    """"password" plus a number is the single most common mutation an attacker's wordlist tries first, so
    the denylist is matched with the digits stripped as well as whole."""
    assert "too common" in password.policy_error("password9999999")
    assert "too common" in password.policy_error("1monkey12345")


def test_a_password_that_reuses_the_email_local_part_is_refused():
    """The local part is the half of an address an attacker always knows, so embedding it — inside a
    longer password or as the whole thing — is not a password the form should accept."""
    assert "email address" in password.policy_error("amirunoel8-2026-pass", email="amirunoel8@gmail.com")
    assert password.policy_error("amirunoel", email="amirunoel8@gmail.com") is not None
    assert password.policy_error("herons-quietly-fold-6", email="amirunoel8@gmail.com") is None


def test_a_short_local_part_does_not_forbid_its_own_letters():
    """`a@b.com` is a real address. A rule that refuses any password containing "a" punishes the user for
    the shape of their mailbox, so the fragment has to have some length to be meaningful."""
    assert password.policy_error("abcdefghijkl", email="a@b.com") is None


def test_the_floor_itself_is_acceptable():
    """The boundary must be usable, or the policy rejects passwords the docs say are long enough."""
    assert password.policy_error("abcdefghijkl") is None
    assert password.MIN_LENGTH == 12


def test_creating_a_hash_cannot_be_routed_around_the_policy():
    """The check lives in create_hash, not only in the request model, so no future caller can store a
    weak hash by skipping validation upstream."""
    with pytest.raises(password.PasswordPolicyError):
        password.create_hash("hunter2")


def test_creating_a_hash_applies_the_email_rule_too():
    """`policy_error` can only check an address against a password if `create_hash` passes it along. A
    router that forgot to call policy_error itself would otherwise store the reusable kind of password."""
    with pytest.raises(password.PasswordPolicyError) as raised:
        password.create_hash("amirunoel8abc", email="amirunoel8@gmail.com")

    assert "email address" in str(raised.value)
    assert password.create_hash("amirunoel8abc", email="someoneelse@work.example")


# ── failure modes must not become server errors ────────────────────────────────


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "not-a-hash",
        "$2b$12$bcryptprefixthatisnotargon2",
        "$argon2id$v=19$m=1,t=1,p=1$c2FsdA$nomac",
    ],
)
def test_a_malformed_stored_hash_fails_the_login_instead_of_raising(stored):
    """The hash column is data. A row written by an older build or a bad migration must cost an attacker
    a failed attempt, not crash the request that touched it."""
    assert password.verify(GOOD, stored) is False


def test_an_empty_candidate_never_matches():
    assert password.verify("", password.create_hash(GOOD)) is False


def test_an_unparseable_hash_is_not_an_argon2_error():
    """The reason `verify` catches two exception trees. argon2-cffi raises InvalidHashError — a
    ValueError — for a hash it cannot parse, and VerifyMismatchError under Argon2Error for a wrong
    password. Catching only the Argon2 tree would let a malformed row escape as a 500, and a fake hasher
    in these tests could never show that."""
    assert issubclass(VerifyMismatchError, Argon2Error)
    assert not issubclass(InvalidHashError, Argon2Error)


def test_an_older_weaker_hash_is_flagged_for_upgrade():
    """Rehashing on a successful login is how costs get raised for existing accounts; the predicate has
    to notice the weaker parameters."""
    weaker = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(GOOD)

    assert password.needs_rehash(weaker) is True
    assert password.needs_rehash(password.create_hash(GOOD)) is False


# ── the enumeration defence ────────────────────────────────────────────────────


def test_blind_verify_does_the_real_work(monkeypatch):
    """The whole point is that the "no such account" branch is not free. If this ever degenerates into
    a sleep or a no-op, the login endpoint becomes an oracle for which addresses are registered."""
    calls = []
    real_verify = password.verify

    def spy(candidate, stored):
        calls.append((candidate, stored))
        return real_verify(candidate, stored)

    monkeypatch.setattr(password, "verify", spy)

    password.blind_verify()

    assert len(calls) == 1
    assert re.match(r"^\$argon2id\$", calls[0][1])
    assert password.verify(GOOD, calls[0][1]) is False
