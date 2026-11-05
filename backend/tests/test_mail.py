"""
tests/test_mail.py — The two transports, and what a failed send costs.

`console` is the default and the one the suite, a fresh clone, and CI use: it must put the whole link in
the log, because that is the only way a reviewer without SMTP credentials can reach a verified account.
`smtp` is exercised against a fake of `smtplib.SMTP_SSL` — asserting the handshake this app depends on
(implicit TLS on 465, login before send, a bounded timeout) without a network.

The security-relevant case is the third one: what gets written to the log when Gmail refuses the
password. A server reply to a failed AUTH command can echo the credential, so the rule is the same one
already applied to /api/health/deep — log the exception class, never its message.
"""

from __future__ import annotations

import smtplib

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


@pytest.fixture
def smtp_transport(monkeypatch):
    monkeypatch.setattr(settings, "mail_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_user", "documind@example.com")
    monkeypatch.setattr(settings, "smtp_password", "aaaaaaaabbbbcccc")
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 465)


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
