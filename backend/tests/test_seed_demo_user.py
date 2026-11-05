"""
tests/test_seed_demo_user.py — The script that gives a reviewer an account.

`scripts/seed_demo_user.py` is what replaces the published access code: one shared, pre-confirmed account
whose password the README states. It writes to a real database, so the properties worth testing are the ones
that would otherwise be discovered on the production cluster — that re-running it does not mint a second
namespace, that the stored hash is the password the operator typed, and that a missing or hopeless password
stops before the first write.
"""

from __future__ import annotations

import pytest

from app.core.password import PasswordPolicyError, needs_rehash, verify
from scripts.seed_demo_user import DEFAULT_EMAIL, main, seed

PASSWORD = "harbour-lights-went-out-4"


def row(fake_users, email=DEFAULT_EMAIL):
    return fake_users.accounts[email.strip().lower()]


def test_it_creates_one_confirmed_account(fake_users):
    result = seed(DEFAULT_EMAIL, PASSWORD)

    assert result["action"] == "created"
    stored = row(fake_users)
    assert stored["email_verified"] is True
    assert stored["verified_at"] is not None
    assert stored["password_hash"].startswith("$argon2id$")


def test_the_hash_it_stores_is_the_password_the_operator_typed(fake_users):
    """The failure this catches is a seed that creates an account nobody can sign in to."""
    seed(DEFAULT_EMAIL, PASSWORD)

    assert verify(PASSWORD, row(fake_users)["password_hash"]) is True
    assert verify("wrong-password-indeed", row(fake_users)["password_hash"]) is False


def test_rerunning_it_keeps_the_account_and_replaces_the_password(fake_users):
    first = seed(DEFAULT_EMAIL, PASSWORD)
    before = row(fake_users)["password_hash"]

    second = seed(DEFAULT_EMAIL, "deckchairs-stacked-7")

    assert second["action"] == "reset"
    assert second["user_id"] == first["user_id"]
    assert fake_users.count_accounts() == 1
    assert row(fake_users)["password_hash"] != before
    assert verify("deckchairs-stacked-7", row(fake_users)["password_hash"]) is True


def test_the_address_is_normalised_before_it_is_written(fake_users):
    """Two spellings would be two accounts, and the demo would log in to whichever it did not mean."""
    seed("Demo@DocuMind.APP  ", PASSWORD)

    assert list(fake_users.accounts) == [DEFAULT_EMAIL]
    assert seed(DEFAULT_EMAIL, PASSWORD)["action"] == "reset"


def test_a_password_that_breaks_the_policy_writes_nothing(fake_users):
    with pytest.raises(PasswordPolicyError):
        seed(DEFAULT_EMAIL, "password123456")

    assert fake_users.count_accounts() == 0


def test_seeding_an_address_reuse_password_is_refused(fake_users):
    """The rule lives in `create_hash`, so a script that skipped request validation still cannot store it."""
    with pytest.raises(PasswordPolicyError):
        seed("demo@documind.app", "documind")

    assert fake_users.count_accounts() == 0


# ── The command line ───────────────────────────────────────────────────────────


def test_running_without_a_password_is_an_error_not_a_default(fake_users, monkeypatch, capsys):
    monkeypatch.delenv("DEMO_ACCOUNT_PASSWORD", raising=False)

    assert main() == 2
    assert "DEMO_ACCOUNT_PASSWORD is not set" in capsys.readouterr().err
    assert fake_users.count_accounts() == 0


def test_a_hopeless_password_stops_before_the_first_write(fake_users, monkeypatch, capsys):
    monkeypatch.setenv("DEMO_ACCOUNT_EMAIL", DEFAULT_EMAIL)
    monkeypatch.setenv("DEMO_ACCOUNT_PASSWORD", "password123456")

    assert main() == 2
    err = capsys.readouterr().err
    assert "will not be accepted" in err
    # The reason is printed; the value that produced it is not.
    assert "password123456" not in err
    assert fake_users.count_accounts() == 0


def test_dry_run_reports_the_cluster_without_touching_it(fake_users, monkeypatch, capsys):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("DEMO_ACCOUNT_EMAIL", DEFAULT_EMAIL)
    monkeypatch.setenv("DEMO_ACCOUNT_PASSWORD", PASSWORD)

    assert main() == 0
    assert "would write demo@documind.app" in capsys.readouterr().out
    assert fake_users.count_accounts() == 0


def test_the_happy_path_says_what_it_made(fake_users, monkeypatch, capsys):
    monkeypatch.setenv("DEMO_ACCOUNT_EMAIL", DEFAULT_EMAIL)
    monkeypatch.setenv("DEMO_ACCOUNT_PASSWORD", PASSWORD)

    assert main() == 0
    out = capsys.readouterr().out
    assert "created demo@documind.app" in out
    assert PASSWORD not in out
    assert needs_rehash(row(fake_users)["password_hash"]) is False
