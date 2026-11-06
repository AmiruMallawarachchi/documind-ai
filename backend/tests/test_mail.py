"""
tests/test_mail.py — The three transports, and what a failed send costs.

`console` is the default and the one the suite, a fresh clone, and CI use: it must put the whole link in
the log, because that is the only way a reviewer without mail credentials can reach a verified account.
`smtp` is exercised against a fake of `smtplib.SMTP_SSL` — asserting the handshake this app depends on
(implicit TLS on 465, login before send, a bounded timeout) without a network. `brevo` is exercised
against a fake of `urllib.request.urlopen`, because the point of it is that it leaves on port 443:
Render's free instances drop outbound SMTP, so a deployed demo cannot deliver mail any other way.

The security-relevant case is the failure one, and it is the same for both live transports: what gets
written to the log when the provider refuses. A reply to a failed AUTH command, or a 401 body that echoes
the key, can carry the credential — so the rule stays the one already applied to /api/health/deep, log
the exception class, never its message. The exception's *status*, where one exists, is logged: it is an
integer, it cannot carry a credential, and it is the difference between "the mail is broken" and "the
sender address is not verified".
"""

from __future__ import annotations

import io
import json
import smtplib
import urllib.error

import pytest
from pydantic import ValidationError

from app.core import mail
from app.core.config import Settings, settings


class RecordingSMTP:
    """Stands in for the SMTP_SSL context manager and remembers what it was asked to do."""

    instances: list["RecordingSMTP"] = []
    raises: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logined = None
        self.message = None
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, user, password):
        self.logined = (user, password)

    def send_message(self, message):
        if type(self).raises is not None:
            raise type(self).raises
        self.message = message


@pytest.fixture(autouse=True)
def _reset_smtp(monkeypatch):
    RecordingSMTP.instances = []
    RecordingSMTP.raises = None
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", RecordingSMTP)
    return RecordingSMTP


class FakeResponse:
    """The context manager `urlopen` hands back, with the body Brevo actually returns."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RecordingUrlopen:
    """Stands in for `urllib.request.urlopen` and remembers the Request it was handed.

    Installed for every test in this module, so a transport that reaches the network by accident fails
    here rather than sending a real email from CI.
    """

    requests: list["RecordingUrlopen"] = []
    raises: Exception | None = None
    body: bytes = b'{"messageId": "<20261105153000.brevo@api.sendinblue.com>"}'

    def __init__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        type(self).requests.append(self)
        if type(self).raises is not None:
            raise type(self).raises

    def __enter__(self):
        return FakeResponse(type(self).body)

    def __exit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def _reset_urlopen(monkeypatch):
    RecordingUrlopen.requests = []
    RecordingUrlopen.raises = None
    RecordingUrlopen.body = b'{"messageId": "<20261105153000.brevo@api.sendinblue.com>"}'
    monkeypatch.setattr(mail.urllib.request, "urlopen", RecordingUrlopen)
    return RecordingUrlopen


@pytest.fixture
def smtp_transport(monkeypatch):
    monkeypatch.setattr(settings, "mail_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_user", "documind@example.com")
    monkeypatch.setattr(settings, "smtp_password", "aaaaaaaabbbbcccc")
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 465)


@pytest.fixture
def brevo_transport(monkeypatch):
    monkeypatch.setattr(settings, "mail_transport", "brevo")
    monkeypatch.setattr(settings, "brevo_api_key", "xbr-aaaaaaaabbbbcccc")
    monkeypatch.setattr(settings, "brevo_sender", "documind@example.com")


# ── console ────────────────────────────────────────────────────────────────────


def test_the_console_transport_writes_the_link_where_a_developer_can_read_it(caplog):
    """The point of the default transport: a clone with no credentials still gets a verified account.

    If this becomes a `logger.debug` or the body is dropped, local signup silently becomes untestable.
    """
    with caplog.at_level("INFO", logger="documind.mail"):
        mail.send_verification_link("you@example.com", "http://localhost:3000/verify?token=abc")

    assert "http://localhost:3000/verify?token=abc" in caplog.text
    assert "you@example.com" in caplog.text


def test_the_console_transport_opens_no_socket(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the console transport must not reach for the network")

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", explode)
    monkeypatch.setattr(mail.urllib.request, "urlopen", explode)
    assert settings.mail_transport == "console"

    mail.send(to="you@example.com", subject="s", body="b")


# ── smtp ───────────────────────────────────────────────────────────────────────


def test_an_smtp_send_logs_in_then_sends_over_implicit_tls(smtp_transport):
    mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    (server,) = RecordingSMTP.instances
    assert (server.host, server.port) == ("smtp.gmail.com", 465)
    assert server.logined == ("documind@example.com", "aaaaaaaabbbbcccc")
    assert server.message["To"] == "you@example.com"
    assert "https://app.example.com/verify?token=abc" in server.message.get_content()


def test_the_sender_is_the_authenticated_account(smtp_transport):
    """Gmail rejects a message whose From is not the logged-in address, with an error that reads like an
    authentication problem rather than a header problem — so the From is not free text here."""
    mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    assert RecordingSMTP.instances[0].message["From"] == "DocuMind <documind@example.com>"


def test_a_hang_is_cut_off_at_a_bounded_timeout(smtp_transport):
    """Render's free instance runs one process. A server that accepts the connection and then says
    nothing would otherwise hold a worker thread for the OS default, which is minutes."""
    mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    assert RecordingSMTP.instances[0].timeout == mail.SMTP_TIMEOUT_SECONDS


def test_the_body_explains_the_link_works_once(smtp_transport):
    mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    body = RecordingSMTP.instances[0].message.get_content()
    assert "Confirm this address" in body
    assert "ignore this message" in body


# ── brevo ──────────────────────────────────────────────────────────────────────


def payload_of(call: RecordingUrlopen) -> dict:
    """The JSON body the transport actually posted, decoded once for the assertions below."""
    return json.loads(call.request.data.decode("utf-8"))


def test_a_brevo_send_posts_the_link_to_the_api_over_https(brevo_transport):
    """The whole reason this transport exists: Render's free instances drop outbound traffic to ports 25,
    465 and 587, so a message that has to leave the host has to leave on 443."""
    mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    (call,) = RecordingUrlopen.requests
    assert call.request.full_url == mail.BREVO_URL
    assert call.request.full_url.startswith("https://")
    assert call.request.get_method() == "POST"
    assert payload_of(call)["to"] == [{"email": "you@example.com"}]
    assert "https://app.example.com/verify?token=abc" in payload_of(call)["textContent"]


def test_the_body_goes_in_the_field_brevo_names(brevo_transport):
    """`plainContent` is what SMTP calls it and what a plausible reading of the API suggests; `textContent`
    is what Brevo's schema actually takes. Posting the other name does not fail loudly on the way out — it
    arrives as a 400 for a missing parameter, which reads like an account problem and sent a whole afternoon
    into sender verification. So the key is asserted here rather than left to the transport's good manners."""
    mail.send(to="you@example.com", subject="s", body="the message")

    (call,) = RecordingUrlopen.requests
    payload = payload_of(call)

    assert payload["textContent"] == "the message"
    assert "plainContent" not in payload


def test_the_brevo_request_authenticates_with_the_key_header(brevo_transport):
    """A key in a header is not a key in the URL: the URL survives a proxy log and a stack trace, and the
    body survives a request dump, so neither of them is where a credential belongs."""
    mail.send(to="you@example.com", subject="s", body="b")

    (call,) = RecordingUrlopen.requests
    assert call.request.get_header("Api-key") == "xbr-aaaaaaaabbbbcccc"
    assert call.request.get_header("Content-type") == "application/json"
    assert "xbr-aaaaaaaabbbbcccc" not in call.request.full_url
    assert "xbr-aaaaaaaabbbbcccc" not in call.request.data.decode("utf-8")


def test_the_brevo_sender_is_a_setting_of_its_own(brevo_transport):
    """Brevo refuses a sender it has not verified, and that refusal reads like a mail outage rather than
    "this address is yours to use" — so the sender is its own setting, not borrowed from the SMTP account."""
    mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    assert payload_of(RecordingUrlopen.requests[0])["sender"] == {
        "name": "DocuMind",
        "email": "documind@example.com",
    }


def test_a_brevo_hang_is_cut_off_at_a_bounded_timeout(brevo_transport):
    mail.send(to="you@example.com", subject="s", body="b")

    assert RecordingUrlopen.requests[0].timeout == mail.HTTP_TIMEOUT_SECONDS


def test_the_brevo_message_id_is_logged_so_a_send_can_be_traced(brevo_transport, caplog):
    """The one handle on a message after it leaves: "the visitor never got it" becomes a lookup at the
    provider instead of a guess, and the id is neither a credential nor a recipient address."""
    with caplog.at_level("INFO", logger="documind.mail"):
        mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    assert "20261105153000.brevo@api.sendinblue.com" in caplog.text


def test_a_rejected_key_is_logged_as_a_class_never_as_a_message(brevo_transport, caplog):
    """Same rule as the SMTP branch and the same reason: a provider that echoes the credential in its 401
    body would otherwise put it into the Render log stream, readable by anyone with dashboard access."""

    class EchoingKeyError(urllib.error.URLError):
        def __str__(self):
            return "401 Unauthorized: invalid api-key xbr-aaaaaaaabbbbcccc"

    RecordingUrlopen.raises = EchoingKeyError("401 Unauthorized")

    with caplog.at_level("ERROR", logger="documind.mail"):
        with pytest.raises(mail.MailError):
            mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    assert "EchoingKeyError" in caplog.text
    assert "xbr-aaaaaaaabbbbcccc" not in caplog.text


def test_a_brevo_refusal_is_mail_error_not_a_500(brevo_transport):
    """A visitor who cannot get a link deserves "try again in a moment", not a stack trace and not the 202
    that would promise a mail that is never coming."""
    RecordingUrlopen.raises = urllib.error.HTTPError(mail.BREVO_URL, 401, "Unauthorized", {}, None)

    with pytest.raises(mail.MailError):
        mail.send(to="you@example.com", subject="s", body="b")


def test_a_refusal_response_body_is_never_read_into_the_log(brevo_transport, caplog):
    """`HTTPError` carries the provider's error body on `fp`, and that body is exactly where Brevo puts
    "unauthorized api-key <the key you just sent>". The branch logs `exc.code`, an integer, and never
    touches the body — so a key sitting in the response stays out of the log stream."""
    RecordingUrlopen.raises = urllib.error.HTTPError(
        mail.BREVO_URL,
        401,
        "Unauthorized",
        {},
        io.BytesIO(b'{"code": "invalid_key", "message": "unauthorized api-key xbr-aaaaaaaabbbbcccc"}'),
    )

    with caplog.at_level("ERROR", logger="documind.mail"):
        with pytest.raises(mail.MailError):
            mail.send(to="you@example.com", subject="s", body="b")

    assert "status=401" in caplog.text
    assert "xbr-aaaaaaaabbbbcccc" not in caplog.text


@pytest.mark.parametrize("status", [401, 400, 453])
def test_the_provider_status_is_logged_because_it_is_the_whole_diagnosis(
    brevo_transport, caplog, status
):
    """`error=HTTPError` is three unrelated faults wearing one name: a wrong key, a sender Brevo will not
    mail from, an account not cleared to send. The status tells them apart, and without it the person
    reading the dashboard log cannot say which of the three to go and fix."""
    RecordingUrlopen.raises = urllib.error.HTTPError(mail.BREVO_URL, status, "Reason", {}, None)

    with caplog.at_level("ERROR", logger="documind.mail"):
        with pytest.raises(mail.MailError):
            mail.send(to="you@example.com", subject="s", body="b")

    assert f"status={status}" in caplog.text


# ── failure ────────────────────────────────────────────────────────────────────


def test_a_rejected_password_is_logged_as_a_class_never_as_a_message(smtp_transport, caplog):
    """The reason this module reports `type(exc).__name__`: some servers echo the AUTH argument back in
    the reply text, so a message-only log line can put an App Password into the Render log stream."""

    class EchoingAuthError(smtplib.SMTPAuthenticationError):
        def __str__(self):
            return "535 5.7.8 Username and Password not accepted. login aaaaaaaabbbbcccc"

    RecordingSMTP.raises = EchoingAuthError(535, b"login aaaaaaaabbbbcccc")

    with caplog.at_level("ERROR", logger="documind.mail"):
        with pytest.raises(mail.MailError):
            mail.send_verification_link("you@example.com", "https://app.example.com/verify?token=abc")

    assert "EchoingAuthError" in caplog.text
    assert "aaaaaaaabbbbcccc" not in caplog.text


def test_a_timeout_becomes_mail_error_not_a_hung_request(smtp_transport):
    RecordingSMTP.raises = TimeoutError("timed out")

    with pytest.raises(mail.MailError):
        mail.send(to="you@example.com", subject="s", body="b")


# ── configuration ──────────────────────────────────────────────────────────────


def build(**overrides) -> Settings:
    """A Settings with the required fields satisfied, so a test can ask about exactly one of them.

    Field names, not environment-variable spellings: pydantic-settings forbids extra init kwargs, and an
    uppercase `SMTP_USER=` here fails as "extra input" instead of exercising the validator — a test that
    would go green for the wrong reason.
    """
    base = {
        "groq_api_key": "k",
        "pinecone_api_key": "k",
        "pinecone_index_name": "i",
        "mongodb_uri": "mongodb://localhost:27017",
        "jwt_secret": "x" * 40,
        "demo_access_code": "abcdef",
        "cors_origins": "http://localhost:3000",
    }
    return Settings(**{**base, **overrides})


def test_smtp_without_credentials_is_refused_at_boot():
    """A dashboard typo in MAIL_TRANSPORT would otherwise surface as a visitor waiting for an email that
    never leaves the process. Fail loudly where the fix is obvious."""
    with pytest.raises(ValidationError) as raised:
        build(mail_transport="smtp", smtp_user="", smtp_password="")

    assert "SMTP_PASSWORD" in str(raised.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mail_transport": "smtp", "smtp_user": "a@b.com", "smtp_password": ""},
        {"mail_transport": "smtp", "smtp_user": "", "smtp_password": "pw"},
        {"mail_transport": "smtp", "smtp_user": "   ", "smtp_password": "  "},
    ],
    ids=["no-password", "no-user", "whitespace-only"],
)
def test_a_half_configured_smtp_transport_is_refused(overrides):
    """Whitespace counts as missing: a Render secret left as an empty line arrives as a string, and a
    login attempt against it would fail at send time rather than at boot."""
    with pytest.raises(ValidationError):
        build(**overrides)


def test_brevo_without_credentials_is_refused_at_boot():
    """The same boot-time rule as SMTP, for the same reason: an empty secret in the dashboard is
    indistinguishable from a working configuration until a visitor is waiting for a link."""
    with pytest.raises(ValidationError) as raised:
        build(mail_transport="brevo", brevo_api_key="", brevo_sender="")

    assert "BREVO_API_KEY" in str(raised.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mail_transport": "brevo", "brevo_api_key": "k", "brevo_sender": ""},
        {"mail_transport": "brevo", "brevo_api_key": "", "brevo_sender": "a@b.com"},
        {"mail_transport": "brevo", "brevo_api_key": "  ", "brevo_sender": "  "},
    ],
    ids=["no-sender", "no-key", "whitespace-only"],
)
def test_a_half_configured_brevo_transport_is_refused(overrides):
    with pytest.raises(ValidationError):
        build(**overrides)


def test_the_brevo_transport_needs_no_smtp_credentials():
    """The two live transports are alternatives, not layers: a deployment that posts to an API must not be
    made to carry a Gmail App Password it will never use — and would then have to rotate."""
    built = build(mail_transport="brevo", brevo_api_key="k", brevo_sender="a@b.com")

    assert (built.smtp_user, built.smtp_password) == ("", "")
    assert built.mail_transport == "brevo"


def test_an_unknown_transport_is_refused_rather_than_falling_back():
    """`MAIL_TRANSPORT=sendgrid` must not quietly become `console`, which would look like a working deploy
    while writing every visitor's verification link into the application log."""
    with pytest.raises(ValidationError):
        build(mail_transport="sendgrid", smtp_user="a@b.com", smtp_password="pw")


def test_the_console_transport_needs_no_credentials_at_all():
    """The default stays bootable with nothing set, which is what keeps a fresh clone runnable."""
    assert build(mail_transport="console").mail_transport == "console"


def test_a_trailing_slash_in_the_frontend_url_does_not_double_up():
    """The verification link is concatenated, and `https://app/` + `/verify` becomes `https://app//verify`
    — a link the browser resolves to a path the Next.js router does not have."""
    assert build(frontend_base_url="https://documind.example.com//").frontend_origin == (
        "https://documind.example.com"
    )
