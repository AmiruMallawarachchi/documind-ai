"""
tests/test_auth.py — Session tokens, owner forging, rate limits, and request tracing.

Phase 5's promise is narrow and worth stating precisely: a deployed instance decides who you are from a
signed token, not from a header you typed. Each test below attacks one way that promise could fail — no
token, wrong code, forged signature, expired token, someone else's owner id — and then checks the two
limits that keep one client from spending the shared free-tier budget.

`production_mode` lives in conftest because the account tests mint real tokens too: it is a fixture rather
than an env var because the rule is read at request time, and it keeps the other 70-odd tests on the
X-Demo-Key path they were written for.
"""

from __future__ import annotations

import jwt
import pytest

from app.core.config import settings
from app.core.rate_limit import SlidingWindowLimiter
from app.core.security import access_code_matches, mint_session, owner_from_authorization

ACCESS_CODE = "test-access-code"
PRODUCTION_HEADERS = {"Origin": "http://localhost:3000"}


def session(client, **overrides):
    return client.post("/api/auth/session", json={"access_code": ACCESS_CODE, **overrides})


# ── Session endpoint ───────────────────────────────────────────────────────────


def test_a_valid_code_opens_a_session_and_issues_a_token(client):
    response = session(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"].count(".") == 2  # header.payload.signature
    assert body["owner_id"]
    assert body["expires_at"]


def test_the_wrong_code_is_refused_without_issuing_anything(client):
    response = client.post("/api/auth/session", json={"access_code": "guess-me"})

    assert response.status_code == 401
    assert "access_token" not in response.json()


def test_a_returning_device_keeps_its_owner_id(client):
    first = session(client, device_key="owner-a")
    second = session(client, device_key="owner-a")

    assert first.json()["owner_id"] == second.json()["owner_id"] == "owner-a"


def test_a_blank_device_key_is_rejected_but_a_missing_one_is_fine(client):
    assert session(client, device_key="   ").status_code == 400
    assert session(client).status_code == 200


def test_an_oversized_device_key_is_rejected(client):
    # Rejected by the schema's own bound rather than by the security helper: the contract says 128, and a
    # 422 is the honest answer to a request that does not match it.
    assert session(client, device_key="x" * 200).status_code == 422


# ── What the token buys ────────────────────────────────────────────────────────


def test_production_refuses_a_request_with_no_token(production_mode, client):
    assert client.get("/api/documents", headers=PRODUCTION_HEADERS).status_code == 401


def test_production_refuses_the_demo_key_that_dev_mode_allows(production_mode, client):
    response = client.get("/api/documents", headers={"X-Demo-Key": "owner-a"})

    # The header is not merely ignored in production: ignoring it would silently fold a caller into the
    # default namespace and show them someone else's documents.
    assert response.status_code == 401


def test_a_token_unlocks_exactly_its_own_namespace(production_mode, client):
    mine = session(client).json()
    theirs = session(client, device_key="other-owner").json()

    assert client.get("/api/documents", headers={"Authorization": f"Bearer {mine['access_token']}"}).status_code == 200

    # Seed a document for the second owner, then read with the first: isolation has to hold with real
    # tokens in play, not only with the demo header the earlier suite uses.
    upload_headers = {"Authorization": f"Bearer {theirs['access_token']}"}
    client.post(
        "/api/upload",
        headers=upload_headers,
        files={"files": ("lease.txt", b"The lessee shall pay rent monthly.", "text/plain")},
    )
    listed = client.get("/api/documents", headers={"Authorization": f"Bearer {mine['access_token']}"})

    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": "Bearer not.a.jwt"},
        {"Authorization": f"Bearer {jwt.encode({'sub': 'owner-a'}, 'a-different-secret-long-enough-for-hmac-sha256', algorithm='HS256')}"},
        {"Authorization": f"Bearer {jwt.encode({'sub': 'owner-a'}, settings.jwt_secret, algorithm='HS256')}"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer "},
    ],
    ids=["garbage", "wrong-secret", "no-expiry", "wrong-scheme", "empty-credential"],
)
def test_unusable_credentials_are_401_not_500(production_mode, client, header):
    assert client.get("/api/documents", headers=header).status_code == 401


def test_an_expired_token_says_so_distinctly(monkeypatch, client):
    monkeypatch.setattr(settings, "jwt_ttl_minutes", -1)
    token, _ = mint_session("owner-a")

    with pytest.raises(Exception) as raised:
        owner_from_authorization(f"Bearer {token}")
    assert "expired" in str(raised.value).lower()


def test_a_token_signed_for_one_owner_cannot_be_rewritten(client, monkeypatch):
    """Tampering with `sub` breaks the signature — the whole point of signing it in the first place."""
    token, _ = mint_session("owner-a")
    header, payload, signature = token.split(".")
    forged = jwt.encode({"sub": "victim"}, settings.jwt_secret, algorithm="HS256")
    tampered = f"{header}.{forged.split('.')[1]}.{signature}"

    assert tampered != token
    with pytest.raises(Exception):
        owner_from_authorization(f"Bearer {tampered}")


def test_the_access_code_is_compared_without_leaking_its_shape():
    assert access_code_matches(ACCESS_CODE) is True
    assert access_code_matches("") is False
    assert access_code_matches("test-access-cod") is False


# ── Rate limits ────────────────────────────────────────────────────────────────


def test_chat_is_limited_per_owner(production_mode, client, monkeypatch):
    monkeypatch.setattr(settings, "chat_requests_per_minute", 2)
    token = session(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Every one of these answers "you have no documents yet" over SSE, which is still a 200 — the limit is
    # about how much work a caller may ask for, not about whether the answer was useful.
    bodies = [client.post("/api/chat", headers=headers, json={"question": "What is the fee?"}).status_code for _ in range(4)]

    assert bodies == [200, 200, 429, 429]


def test_a_rejected_request_advises_when_to_return(production_mode, client, monkeypatch):
    monkeypatch.setattr(settings, "chat_requests_per_minute", 1)
    token = session(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/chat", headers=headers, json={"question": "What is the fee?"})
    rejected = client.post("/api/chat", headers=headers, json={"question": "And the term?"})

    assert rejected.status_code == 429
    assert int(rejected.headers["retry-after"]) >= 1
    assert "Try again in" in rejected.json()["detail"]


def test_one_owner_hitting_the_limit_does_not_silence_another(production_mode, client, monkeypatch):
    monkeypatch.setattr(settings, "chat_requests_per_minute", 1)
    first = session(client, device_key="owner-a").json()["access_token"]
    second = session(client, device_key="owner-b").json()["access_token"]

    # Both answers are refused for having no documents, but the first is refused by the limiter and the
    # second by the empty-corpus guard — which is what proves the buckets are per owner.
    client.post("/api/chat", headers={"Authorization": f"Bearer {first}"}, json={"question": "Q"})
    blocked = client.post("/api/chat", headers={"Authorization": f"Bearer {first}"}, json={"question": "Q"})
    allowed = client.post("/api/chat", headers={"Authorization": f"Bearer {second}"}, json={"question": "Q"})

    assert blocked.status_code == 429
    assert allowed.status_code == 200


def test_uploads_are_limited_too(production_mode, client, monkeypatch):
    monkeypatch.setattr(settings, "upload_requests_per_minute", 1)
    token = session(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    def upload():
        return client.post(
            "/api/upload", headers=headers, files={"files": ("a.txt", b"text body", "text/plain")}
        )

    assert upload().status_code == 202
    assert upload().status_code == 429


def test_sessions_are_limited_per_address(client, monkeypatch):
    monkeypatch.setattr(settings, "login_requests_per_minute", 2)

    codes = [session(client).status_code for _ in range(3)]

    assert codes == [200, 200, 429]


def test_the_window_slides_instead_of_resetting():
    """The unit the HTTP tests cannot reach: the maths that makes this a sliding window."""
    limiter = SlidingWindowLimiter()

    assert limiter.check("b", "k", limit=2, window_seconds=60) == 0.0
    assert limiter.check("b", "k", limit=2, window_seconds=60) == 0.0
    retry_after = limiter.check("b", "k", limit=2, window_seconds=60)

    assert 0 < retry_after <= 60
    # A different key in the same bucket is a different budget.
    assert limiter.check("b", "other", limit=2, window_seconds=60) == 0.0


def test_a_limit_of_zero_is_treated_as_unlimited():
    limiter = SlidingWindowLimiter()

    assert all(limiter.check("b", "k", limit=0) == 0.0 for _ in range(5))


# ── Request tracing ────────────────────────────────────────────────────────────


def test_every_response_carries_a_request_id(client):
    response = client.get("/api/documents")

    assert response.headers["x-request-id"]


def test_a_client_supplied_request_id_is_echoed_and_used_in_the_log(client, caplog):
    with caplog.at_level("INFO", logger="documind.access"):
        response = client.get("/api/documents", headers={"X-Request-ID": "turn-42"})

    assert response.headers["x-request-id"] == "turn-42"
    lines = [record for record in caplog.records if record.name == "documind.access"]
    assert [line.request_id for line in lines] == ["turn-42"]
    assert lines[0].method == "GET"
    assert lines[0].status == 200


def test_health_pings_do_not_fill_the_log(client, caplog):
    with caplog.at_level("INFO", logger="documind.access"):
        client.get("/api/health")

    assert [r for r in caplog.records if r.name == "documind.access"] == []


def test_an_overlong_request_id_is_trimmed_rather_than_reflected(client):
    response = client.get("/api/documents", headers={"X-Request-ID": "z" * 500})

    assert len(response.headers["x-request-id"]) <= 64
