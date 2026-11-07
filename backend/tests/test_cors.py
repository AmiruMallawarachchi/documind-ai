"""
tests/test_cors.py — The preflight answers a browser needs before it will send.

WHY this file exists separately: `allow_methods` is checked by the browser and by nothing else in this
suite. TestClient calls the route directly and never asks permission first, so a verb missing from the
list is invisible to every other test — #35 shipped `PATCH /api/conversations/{id}` with the whole
backend suite green, and the rename button on the deployed page could not reach it. The browser paid for
that with a 400 on the OPTIONS and one line of "That did not reach the server."

Every assertion here is about the preflight response, not the route behind it. The routes themselves are
covered with their real behaviour in test_api.py and test_auth.py.
"""

from __future__ import annotations

ORIGIN = "http://localhost:3000"


def preflight(
    client,
    method: str,
    path: str,
    origin: str = ORIGIN,
    headers: str | None = "authorization,content-type",
):
    """Ask the server the question a browser asks before a cross-origin write.

    `headers=None` leaves `Access-Control-Request-Headers` out of the request entirely, which is what a
    plain GET does; sending it as an empty string is not the same thing and Starlette reads it as a
    request for a header literally named "".
    """
    request_headers = {
        "Origin": origin,
        "Access-Control-Request-Method": method,
    }
    if headers is not None:
        request_headers["Access-Control-Request-Headers"] = headers

    return client.options(path, headers=request_headers)


# ── What the frontend actually sends ──────────────────────────────────────────


def test_every_verb_the_frontend_uses_is_allowed_on_every_path_it_uses_it_on(client):
    # One entry per call in `frontend/lib/api.ts`. A new verb there needs a partner here, and the list
    # being visible in one place is what makes that requirement findable.
    writes = [
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/register"),
        ("POST", "/api/upload"),
        ("POST", "/api/chat"),
        ("PATCH", "/api/conversations/abc123"),
        ("DELETE", "/api/conversations/abc123"),
        ("POST", "/api/conversations/abc123/share"),
        ("DELETE", "/api/conversations/abc123/share"),
    ]

    for method, path in writes:
        response = preflight(client, method, path)
        allowed = response.headers.get("access-control-allow-methods", "")

        assert response.status_code == 200, f"{method} {path} was refused its own preflight"
        assert method in allowed, f"{method} is missing from the allow-list on {path}: {allowed}"


def test_the_read_the_shared_page_makes_is_allowed_without_credentials(client):
    # `/api/shared/{token}` is fetched by a browser with no session at all, and the preflight must not
    # invent a credentialed one just because the rest of the API is cookie-or-token.
    response = preflight(client, "GET", "/api/shared/abcdef", headers=None)

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_a_preflighted_write_answers_the_same_as_the_write_itself(client):
    # The preflight is only worth anything if the real request that follows it is allowed by the same
    # settings; this is the pair that a wildcard `allow_methods` would pass by accident.
    session = preflight(client, "PATCH", "/api/conversations/abc123")
    direct = client.patch("/api/conversations/abc123", json={"pinned": True}, headers={"X-Demo-Key": "owner-a"})

    assert session.status_code == 200
    assert direct.status_code == 404  # no such thread for this owner; the point is that CORS let it through


# ── What the list still refuses ───────────────────────────────────────────────


def test_a_verb_the_frontend_does_not_use_is_still_refused(client):
    """`allow_methods` is a list, not `*`, and this is the test that keeps it one.

    Turning the whole thing into a wildcard would make every test above pass and would let any page in the
    world put this API's writes behind a stranger's click.
    """
    assert preflight(client, "PUT", "/api/conversations/abc123").status_code == 400


def test_an_unknown_origin_gets_no_allow_header(client):
    response = preflight(client, "PATCH", "/api/conversations/abc123", origin="https://not-documind.example")

    assert "access-control-allow-origin" not in response.headers
