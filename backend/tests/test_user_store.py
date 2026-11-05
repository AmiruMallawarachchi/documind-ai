"""
tests/test_user_store.py — Accounts, against a fake that enforces the unique index for real.

The interesting failure this guards is not "did a row get written", it is "did two rows get written for
one address". A fake collection that accepts every insert would let a duplicate-hash account through and
the signup endpoint would still look correct — the same class of bug the `filter=`/`filters=` leak was,
so this fake mirrors production's permissiveness exactly: equality plus the two unique indexes declared
at cold start.

Everything else here is about what a row must never contain or return: a plaintext password, a Mongo
`_id`, or an email that differs in case from the one that was written.
"""

from __future__ import annotations

from datetime import timezone

import pytest
from pymongo.errors import DuplicateKeyError

from app.services import metadata_store, user_store

HASH = "$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHRzYWx0$abcdefghijabcdefghijabcdefghijabcd"


class _Result:
    """Stands in for pymongo's UpdateResult: user_store reads matched_count."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeUsersCollection:
    """Stores rows and refuses a write that would break an index the real collection declares."""

    def __init__(self, db):
        self._db = db

    def _conflict(self, record):
        for field in self._db.unique_fields:
            if self._db.find_existing(field, record.get(field)) is not None:
                return field
        return None

    def insert_one(self, record):
        conflict = self._conflict(record)
        self._db.log.append(("users.insert_one", dict(record), conflict))
        if conflict is not None:
            raise DuplicateKeyError(f"E11000 duplicate key error: {conflict}_1")
        stored = dict(record)
        # A real document carries the internal id whether or not the writer supplied one.
        stored.setdefault("_id", "507f1f77bcf86cd799439011")
        self._db.rows.append(stored)

    def find_one(self, flt, projection=None):
        self._db.log.append(("users.find_one", flt, projection))
        for row in self._db.rows:
            if all(row.get(key) == value for key, value in flt.items()):
                if projection and projection.get("_id") == 0:
                    return {key: value for key, value in row.items() if key != "_id"}
                return dict(row)
        return None

    def update_one(self, flt, update):
        self._db.log.append(("users.update_one", flt, update))
        matched = 0
        for row in self._db.rows:
            if all(row.get(key) == value for key, value in flt.items()):
                row.update(update["$set"])
                matched += 1
        return _Result(matched_count=matched, modified_count=matched)

    def count_documents(self, flt):
        self._db.log.append(("users.count_documents", flt))
        return len(self._db.rows)


class FakeUsersDB:
    """The accounts slice of the fake database, told which fields its unique indexes cover."""

    def __init__(self):
        self.log: list[tuple] = []
        self.rows: list[dict] = []
        self.unique_fields: set[str] = set()
        self._users = FakeUsersCollection(self)

    def find_existing(self, field, value):
        for row in self.rows:
            if row.get(field) == value:
                return row
        return None

    def __getitem__(self, name):
        assert name == "users", f"user_store asked for the {name!r} collection"
        return self._users


@pytest.fixture
def db(monkeypatch) -> FakeUsersDB:
    store = FakeUsersDB()
    monkeypatch.setattr(user_store, "_database", lambda: store)
    return store


def logged(store: FakeUsersDB, operation: str) -> list[tuple]:
    return [entry for entry in store.log if entry[0] == operation]


# ── writing an account ─────────────────────────────────────────────────────────


def test_a_new_account_is_stored_unverified_with_an_aware_timestamp(db):
    record = user_store.create_account("Amiru@Example.COM", HASH)

    assert record["email"] == "amiru@example.com"
    assert record["email_verified"] is False
    assert record["verified_at"] is None
    assert record["created_at"].tzinfo == timezone.utc


def test_the_store_holds_the_hash_and_never_a_plaintext(db):
    user_store.create_account("amiru@example.com", HASH)

    written = logged(db, "users.insert_one")[-1][1]
    assert written["password_hash"] == HASH
    assert not any("hunter2" in str(value) for value in written.values())


def test_the_account_id_is_random_hex_and_unique_per_signup(db):
    """Not an ObjectId: the id is embedded in a verification link, and a sequential one would let an
    attacker step through the account list and confirm which addresses exist."""
    first = user_store.create_account("a@example.com", HASH)
    second = user_store.create_account("b@example.com", HASH)

    assert first["user_id"] != second["user_id"]
    assert len(first["user_id"]) == 32
    assert all(char in "0123456789abcdef" for char in first["user_id"])


def test_a_second_signup_for_the_same_address_is_refused_by_the_index(db):
    db.unique_fields = {"email"}
    user_store.create_account("amiru@example.com", HASH)

    with pytest.raises(user_store.AccountExistsError):
        user_store.create_account("Amiru@Example.com", HASH)

    assert len(db.rows) == 1


def test_the_email_is_normalised_before_it_becomes_a_key(db):
    """Registering `A@x.com` and logging in as `a@x.com` must find the same row, and the unique index is
    only a defence if both spellings are written identically."""
    user_store.create_account("  Amiru@Example.com  ", HASH)

    assert logged(db, "users.insert_one")[-1][1]["email"] == "amiru@example.com"
    assert user_store.find_by_email("amiru@example.com ") is not None


def test_an_address_too_long_to_be_delivered_is_refused(db):
    with pytest.raises(ValueError):
        user_store.normalize_email("local@" + "x" * user_store.MAX_EMAIL_LENGTH)


# ── reading an account ─────────────────────────────────────────────────────────


def test_a_lookup_asks_for_exactly_the_field_the_index_covers(db):
    user_store.find_by_email("amiru@example.com")
    user_store.find_by_id("abc123")

    flt, _ = logged(db, "users.find_one")[0][1:]
    assert flt == {"email": "amiru@example.com"}
    assert logged(db, "users.find_one")[1][1] == {"user_id": "abc123"}


def test_no_read_hands_back_mongos_internal_id(db):
    """The account row is the closest a raw document gets to an HTTP response, so the projection is
    where `_id` is dropped rather than a router remembering to."""
    user_store.create_account("amiru@example.com", HASH)

    found = user_store.find_by_email("amiru@example.com")

    assert "_id" not in found
    assert logged(db, "users.find_one")[-1][2] == {"_id": 0}


def test_a_missing_account_reads_as_none_rather_than_an_error(db):
    assert user_store.find_by_email("nobody@example.com") is None
    assert user_store.find_by_id("does-not-exist") is None


# ── verification ───────────────────────────────────────────────────────────────


def test_verifying_an_account_stamps_the_time_it_happened(db):
    record = user_store.create_account("amiru@example.com", HASH)

    assert user_store.mark_verified(record["user_id"]) is True

    flt, update = logged(db, "users.update_one")[-1][1:]
    assert flt == {"user_id": record["user_id"], "email_verified": False}
    assert update["$set"]["email_verified"] is True
    assert update["$set"]["verified_at"].tzinfo == timezone.utc
    assert db.rows[0]["email_verified"] is True


def test_re_clicking_a_verification_link_still_succeeds(db):
    """Idempotent, because users do click the same link twice and a 400 the second time would read as a
    broken product. Only the first click changes the row."""
    record = user_store.create_account("amiru@example.com", HASH)
    user_store.mark_verified(record["user_id"])

    assert user_store.mark_verified(record["user_id"]) is True
    assert logged(db, "users.update_one")[-1][1]["email_verified"] is False


def test_verifying_an_unknown_account_reports_false(db):
    assert user_store.mark_verified("never-signed-up") is False


def test_a_verification_never_writes_to_another_accounts_row(db):
    first = user_store.create_account("a@example.com", HASH)
    second = user_store.create_account("b@example.com", HASH)

    user_store.mark_verified(first["user_id"])

    assert second["email_verified"] is False
    assert db.rows[1]["email_verified"] is False


def test_the_account_count_is_available_for_health_and_seeding(db):
    assert user_store.count_accounts() == 0
    user_store.create_account("amiru@example.com", HASH)

    assert user_store.count_accounts() == 1


# ── the index this service depends on ─────────────────────────────────────────


def test_the_email_unique_index_is_declared_at_cold_start():
    """create_account catches DuplicateKeyError, but the error only exists if the index does. Without
    this line the two duplicate signups would both be written and the second one's hash would win."""
    class Recorder:
        def __init__(self):
            self.specs = []

        def __getitem__(self, name):
            return self

        def create_index(self, spec, **kwargs):
            self.specs.append((str(spec), kwargs))

    recorder = Recorder()
    metadata_store._ensure_indexes(recorder)

    users_indexes = [kwargs for spec, kwargs in recorder.specs if spec == "email"]
    assert users_indexes and users_indexes[0]["unique"] is True
