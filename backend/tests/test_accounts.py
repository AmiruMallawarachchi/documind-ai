"""
tests/test_accounts.py — Sign up, confirm an address, sign in — and everything an attacker can learn by
doing those three things in the wrong order.

The HTTP surface is where the account design is actually tested. `test_user_store.py` proves the row shape;
this file proves the promises a stranger can observe:

  - Registering twice for one address returns 202 both times, with the same body, and leaves the first
    account alone.
  - Signing in with an unknown address and signing in with a wrong password are the same answer, and cost
    the same.
  - A verification link cannot be used as a session token, and a session token cannot confirm an address,
    even though both are signed by the same key with the same subject.

`client` brings the fakes from conftest: accounts in a dict, mail captured in a list, no SMTP socket.
`production_mode` flips the environment so a token minted here is exercised against the real
owner-scoping dependency rather than the `X-Demo-Key` shortcut the rest of the suite uses.
"""

from __future__ import annotations

import inspect

import jwt
from argon2 import PasswordHasher

from app.core import mail, password
from app.core.config import settings
from app.core.security import mint_session, mint_verification
from app.services import user_store

TEST_PASSWORD = "herons-quietly-fold-6"
WRONG_PASSWORD = "the-wrong-good-pass"
CONFIRMATION_MESSAGE = "A confirmation link is on its way to that address. It expires in a day."


def register(client, email="amiru@example.com", password_value=TEST_PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password_value})


def token_from(outbox) -> str:
    return outbox[-1]["link"].split("token=")[1]


def login(client, email="amiru@example.com", password_value=TEST_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password_value})


def signup_and_verify(client, outbox, email="amiru@example.com", password_value=TEST_PASSWORD):
    """The whole path a real visitor takes, ending with a confirmed account."""
    assert register(client, email, password_value).status_code == 202
    assert client.post("/api/auth/verify", json={"token": token_from(outbox)}).status_code == 200
    return user_store.find_by_email(email)


# ── register ───────────────────────────────────────────────────────────────────


def test_signup_creates_an_unverified_account_and_posts_a_link(client, fake_mail):
    response = register(client)

    assert response.status_code == 202, response.text
    assert response.json() == {"message": CONFIRMATION_MESSAGE}
    assert user_store.find_by_email("amiru@example.com")["email_verified"] is False
    assert [entry["to"] for entry in fake_mail] == ["amiru@example.com"]


def test_the_link_points_at_the_frontend_with_a_token_for_that_account(client, fake_mail):
    """Built from FRONTEND_BASE_URL rather than the request's Host, because behind Render the Host is the
    API's own domain and the visitor would land on onrender.com."""
    register(client)

    link = fake_mail[0]["link"]
    assert link.startswith(f"{settings.frontend_origin}/verify?token=")
    claims = jwt.decode(
        token_from(fake_mail), settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
    )
    assert claims["sub"] == user_store.find_by_email("amiru@example.com")["user_id"]
    assert claims["purpose"] == "verify"


def test_a_weak_password_is_refused_with_the_reason_from_the_policy(client, fake_mail):
    response = register(client, password_value="Password1234")

    assert response.status_code == 422
    assert "too common" in response.json()["detail"]
    assert fake_mail == []


def test_the_rule_against_reusing_your_address_only_lives_in_the_hasher(client, fake_mail):
    """The schema bounds the length; this rule needs the email alongside the password, which is why
    create_hash takes both rather than leaving every router to remember to check."""
    response = register(client, email="amirunoel8@gmail.com", password_value="amirunoel8abc")

    assert response.status_code == 422
    assert "email address" in response.json()["detail"]


def test_an_invalid_address_is_rejected_before_any_account_is_written(client, fake_mail):
    response = register(client, email="not-an-email")

    assert response.status_code == 422
    assert fake_mail == []


def test_the_password_is_never_serialised_or_reprued():
    """SecretStr is the field type precisely so that a later `logger.debug(payload)` or a `model_dump()`
    added for another reason cannot put a plaintext into the log stream."""
    from app.models.auth import RegisterRequest

    payload = RegisterRequest(email="a@b.com", password=TEST_PASSWORD)

    assert TEST_PASSWORD not in repr(payload)
    assert TEST_PASSWORD not in str(payload.model_dump())


def test_the_account_row_holds_only_a_hash(client, fake_mail):
    register(client)

    record = user_store.find_by_email("amiru@example.com")

    assert record["password_hash"].startswith("$argon2id$")
    assert TEST_PASSWORD not in str(record)


def test_a_second_signup_says_the_same_thing_and_changes_nothing(client, fake_mail):
    """The response cannot reveal whether the address already existed — and the stronger promise is that
    the attempt cannot damage the existing account, because a stranger who can re-register your email must
    not be able to reset your password or un-verify your address."""
    register(client)
    original_hash = user_store.find_by_email("amiru@example.com")["password_hash"]
    client.post("/api/auth/verify", json={"token": token_from(fake_mail)})

    second = register(client, password_value="a-different-good-password")

    assert second.status_code == 202
    assert second.json() == {"message": CONFIRMATION_MESSAGE}
    after = user_store.find_by_email("amiru@example.com")
    assert after["password_hash"] == original_hash
    assert after["email_verified"] is True


# ── verify ─────────────────────────────────────────────────────────────────────


def test_a_valid_link_confirms_the_address(client, fake_mail):
    register(client)

    response = client.post("/api/auth/verify", json={"token": token_from(fake_mail)})

    assert response.status_code == 200
    assert response.json() == {"email": "amiru@example.com"}
    assert user_store.find_by_email("amiru@example.com")["email_verified"] is True


def test_a_session_token_does_not_confirm_an_address(client):
    """Both tokens are HS256, both carry a `sub`, and both are minted by this service. Only the `purpose`
    claim stops a token that unlocks documents from also being proof of email ownership."""
    token, _ = mint_session("user-0001")

    response = client.post("/api/auth/verify", json={"token": token})

    assert response.status_code == 400
    assert "verification" in response.json()["detail"]


def test_a_verification_link_does_not_unlock_the_api(client, fake_mail, production_mode):
    """The mirror image of the test above, and the one that matters more: verification links are emailed,
    forwarded, archived by webmail proxies and pasted into chat. Opening one must not be a session."""
    record = signup_and_verify(client, fake_mail)
    link_token = token_from(fake_mail)

    response = client.get("/api/documents", headers={"Authorization": f"Bearer {link_token}"})

    assert response.status_code == 401
    assert user_store.find_by_id(record["user_id"])["email_verified"] is True


def test_an_expired_link_is_refused(client, fake_mail, monkeypatch):
    register(client)
    monkeypatch.setattr(settings, "verification_ttl_minutes", -1)
    expired, _ = mint_verification(user_store.find_by_email("amiru@example.com")["user_id"])

    response = client.post("/api/auth/verify", json={"token": expired})

    assert response.status_code == 400
    assert "expired" in response.json()["detail"].lower()
    assert user_store.find_by_email("amiru@example.com")["email_verified"] is False


def test_a_forged_link_is_refused(client):
    forged = jwt.encode(
        {"sub": "user-0001", "purpose": "verify", "exp": 9_999_999_999},
        "a-different-secret-long-enough-for-hmac-sha256",
        algorithm="HS256",
    )

    assert client.post("/api/auth/verify", json={"token": forged}).status_code == 400


def test_a_correctly_signed_link_for_an_unknown_account_is_refused(client):
    """Signed by us for an id that is no longer in the database. Same answer as any other bad link, which
    is what keeps this endpoint from becoming a probe."""
    token, _ = mint_verification("nobody-at-all")

    response = client.post("/api/auth/verify", json={"token": token})

    assert response.status_code == 400
    assert "not valid" in response.json()["detail"]


def test_clicking_a_link_twice_is_not_an_error(client, fake_mail):
    register(client)
    token = token_from(fake_mail)

    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200


# ── resend ─────────────────────────────────────────────────────────────────────


def test_resending_to_an_unverified_address_sends_again(client, fake_mail):
    register(client)
    fake_mail.clear()

    response = client.post("/api/auth/resend", json={"email": "amiru@example.com"})

    assert response.status_code == 202
    assert len(fake_mail) == 1


def test_resend_says_the_same_thing_for_an_unknown_address(client, fake_mail):
    response = client.post("/api/auth/resend", json={"email": "nobody@example.com"})

    assert response.status_code == 202
    assert response.json() == {"message": CONFIRMATION_MESSAGE}
    assert fake_mail == []


def test_resend_does_not_mail_a_confirmed_address(client, fake_mail):
    signup_and_verify(client, fake_mail)
    fake_mail.clear()

    assert client.post("/api/auth/resend", json={"email": "amiru@example.com"}).status_code == 202
    assert fake_mail == []


def test_a_mail_failure_is_a_502_and_not_a_promise(client, fake_mail, monkeypatch):
    """The one place the generic-202 rule is allowed to break. Saying "check your inbox" when nothing left
    the process strands a visitor; the account row still exists, so /auth/resend can retry."""

    def boom(to, link):
        raise mail.MailError("relay refused")

    monkeypatch.setattr(mail, "send_verification_link", boom)

    response = register(client)

    assert response.status_code == 502
    assert user_store.find_by_email("amiru@example.com")["email_verified"] is False


# ── login ──────────────────────────────────────────────────────────────────────


def test_a_confirmed_account_signs_in_and_gets_its_own_namespace(client, fake_mail, production_mode):
    record = signup_and_verify(client, fake_mail)

    # The address is sent in a different case and with a space, exactly as a person types it, and must
    # still find the row that the normalised signup insert wrote.
    response = login(client, email=" AMIRU@example.com ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["owner_id"] == record["user_id"]
    documents = client.get("/api/documents", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert documents.status_code == 200


def test_an_unverified_account_is_told_to_confirm(client, fake_mail):
    register(client)

    response = login(client)

    assert response.status_code == 403
    assert "Confirm your email" in response.json()["detail"]


def test_a_wrong_password_on_a_real_account_is_a_401(client, fake_mail):
    signup_and_verify(client, fake_mail)

    assert login(client, password_value=WRONG_PASSWORD).status_code == 401


def test_an_unknown_address_and_a_wrong_password_are_the_same_answer(client, fake_mail, monkeypatch):
    """Identical status and body — and the unknown branch has to cost the same too, which is what
    `blind_verify` is for. A 1 ms "no such user" against a 60 ms hash verification is an account oracle,
    so this asserts the call happened rather than measuring milliseconds."""
    signup_and_verify(client, fake_mail, email="second@example.com")
    blind_calls = []
    monkeypatch.setattr(password, "blind_verify", lambda: blind_calls.append(1))

    unknown = login(client, email="never-registered@example.com")
    wrong = login(client, email="second@example.com", password_value=WRONG_PASSWORD)

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": "Wrong email or password."}
    assert blind_calls == [1]


def test_an_oversized_password_is_rejected_without_hashing(client, fake_mail):
    """The length bound lives on the schema, before the KDF, so a 1 MB body cannot make the server hash
    it. 422 rather than 401 is the honest answer to a request that does not match the contract."""
    response = login(client, password_value="x" * 2000)

    assert response.status_code == 422


def test_a_hash_made_with_older_parameters_is_upgraded_on_sign_in(client, fake_mail, fake_users):
    """Raising the cost in core/password.py is only an improvement if existing accounts move onto it, and
    a successful sign-in is the one moment the plaintext is available to do it with."""
    signup_and_verify(client, fake_mail)
    stored = fake_users.accounts["amiru@example.com"]
    weaker = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(TEST_PASSWORD)
    stored["password_hash"] = weaker

    assert login(client).status_code == 200

    assert stored["password_hash"] != weaker
    assert password.needs_rehash(stored["password_hash"]) is False
    assert password.verify(TEST_PASSWORD, stored["password_hash"]) is True


# ── limits and shape ───────────────────────────────────────────────────────────


def test_signup_is_limited_per_address_over_an_hour(client, fake_mail, monkeypatch):
    """The window, not the number, is the defence: five per minute would still let a script open hundreds
    of accounts an hour into a 512 MB Atlas cluster and send hundreds of mails through one Gmail account."""
    monkeypatch.setattr(settings, "signup_requests_per_hour", 2)

    codes = [register(client, email=f"person{i}@example.com").status_code for i in range(4)]

    assert codes == [202, 202, 429, 429]


def test_the_login_limit_keys_on_address_not_on_the_account(client, fake_mail, monkeypatch):
    monkeypatch.setattr(settings, "login_requests_per_minute", 2)

    codes = [login(client, email="nobody@example.com").status_code for _ in range(3)]

    assert codes == [401, 401, 429]


def test_the_account_endpoints_are_sync_functions():
    """Not `async def`: each of these does a blocking Mongo write and, on two of them, a ten-second SMTP
    handshake. FastAPI runs a sync endpoint on a worker thread and an `async def` one on the event loop, so
    the wrong keyword here would stall every other in-flight request — including the chat SSE stream — for
    the duration of a send."""
    from app.routers import auth

    for name in ("register", "resend_verification", "verify_email", "login"):
        assert not inspect.iscoroutinefunction(getattr(auth, name)), f"{name} must be sync"


def test_the_old_access_code_flow_still_works(client):
    """This change adds accounts; it does not remove the gate the deployed frontend calls today. The two
    coexist until the UI switches over, and this test is what makes that switch a deliberate commit."""
    response = client.post("/api/auth/session", json={"access_code": "test-access-code"})

    assert response.status_code == 200
    assert response.json()["access_token"].count(".") == 2


def test_a_registration_email_is_not_required_to_be_lowercase(client, fake_mail):
    """One address, one account: the index is on the normalised form, and a signup that stored the typed
    spelling would leave its owner unable to log in with the address they registered."""
    register(client, email="  Amiru@Example.COM ")

    assert user_store.find_by_email("amiru@example.com")["email"] == "amiru@example.com"
    assert fake_mail[0]["to"] == "amiru@example.com"
