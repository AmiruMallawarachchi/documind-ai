"""
core/mail.py — Sending one message, to a log, to Gmail, or to an HTTPS mail API.

Three transports behind one function because the same code has to work in three places with very different
needs. A clone of this repo has no mail credentials, so `console` writes the verification link to the log
and a reviewer can confirm an address without configuring anything. CI needs the same thing without
needing a mail server. And the deployed demo uses `brevo`, because Render's free instances block outbound
traffic to SMTP ports 25, 465 and 587 — `smtp` works on a laptop and can never deliver from there.

Gmail specifics, because they are the reason this is not just `smtplib.SMTP(...)`: port 465 is implicit
TLS, the `SMTP_SSL` class rather than `starttls`, and the password is an **App Password** from
myaccount.google.com/apppasswords, never the account password — Google disabled raw account-password
auth for this endpoint, and an App Password can be revoked on its own.

Failures are reported by exception *class*, never message. A server reply to a failed AUTH command can
echo the credential in its text, and this module is the one place in the app that holds a password. The
same rule covers the API key: it travels in a request header, and `urllib`'s exceptions carry the URL and
the status line but never the headers — which is still not a reason to print anything else.
"""

from __future__ import annotations

import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings

logger = logging.getLogger("documind.mail")

# Bounded so a SMTP host that accepts the TCP connection and then says nothing cannot hold a worker
# thread open indefinitely — Render's free instance runs one process, and every hung send is a request
# that will not be served.
SMTP_TIMEOUT_SECONDS = 10

# Brevo's transactional endpoint. Port 443, so it survives the same host that blocks SMTP.
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
HTTP_TIMEOUT_SECONDS = 10


class MailError(Exception):
    """The message did not leave this process. Routers answer 502; they must not answer 202."""


def send(*, to: str, subject: str, body: str) -> None:
    """Deliver one plain-text message.

    Plain text rather than an HTML template: a verification link is the whole job, it renders in a
    plaintext-only client, and there is no markup here to keep in sync with the frontend's design.
    """
    if settings.mail_transport == "console":
        # The link is the payload, so the console transport prints it instead of dropping it — that is
        # what makes a local run and a fresh clone testable without a mail account at all.
        logger.info("mail (console) to=%s subject=%r\n%s", to, subject, body)
        return

    if settings.mail_transport == "brevo":
        _send_brevo(to=to, subject=subject, body=body)
        return

    _send_smtp(to=to, subject=subject, body=body)


def _send_brevo(*, to: str, subject: str, body: str) -> None:
    """Post one message to Brevo's transactional API.

    stdlib `urllib` rather than an HTTP client library: this is a single POST with a fixed body, and
    `requirements.txt` is the file that decides whether the image still fits in 512 MB — the same
    reasoning that picked `smtplib` over a mail SDK. `urlopen` raises for any non-2xx, so reaching the
    log line at the bottom *is* the delivery confirmation.
    """
    payload = json.dumps(
        {
            "sender": {"name": "DocuMind", "email": settings.brevo_sender},
            "to": [{"email": to}],
            "subject": subject,
            "plainContent": body,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        BREVO_URL,
        data=payload,
        method="POST",
        headers={
            "api-key": settings.brevo_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            message_id = json.loads(response.read()).get("messageId", "")
    except Exception as exc:  # noqa: BLE001 - a transport blowing up must never become a 500.
        # Broad on purpose, for the same reason as the SMTP branch: a 401, a 5xx, a refused connection and
        # a timeout are different exception trees and the caller's answer is the same for all of them.
        logger.error("mail failed to=%s error=%s", to, type(exc).__name__)
        raise MailError("The verification email could not be sent.") from exc

    # Brevo's messageId is the only handle on a send after it leaves, so it goes in the log: "the visitor
    # never got it" becomes a lookup instead of a guess. It is not a credential and not the recipient.
    logger.info("mail sent to=%s via=brevo message_id=%s", to, message_id)


def _send_smtp(*, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = formataddr(("DocuMind", settings.smtp_user))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
        ) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 - a transport blowing up must never become a 500.
        # Broad on purpose. An auth rejection, a TLS failure and a timeout are three different exception
        # trees in the stdlib and the caller's response is the same for all of them: try again shortly.
        # The class name is logged rather than the message because a server's reply to a failed AUTH
        # command can echo the credential in its text.
        logger.error("mail failed to=%s error=%s", to, type(exc).__name__)
        raise MailError("The verification email could not be sent.") from exc

    logger.info("mail sent to=%s via=%s", to, settings.smtp_host)


def send_verification_link(to: str, link: str) -> None:
    """The one message this app sends. Kept as its own function so the copy lives in the code, not in a
    dashboard, and a test can assert what a visitor is actually told."""
    body = (
        "You created a DocuMind account.\n\n"
        "Confirm this address to finish signing up:\n\n"
        f"{link}\n\n"
        "The link works once and stays valid for a day. If you did not create this account, you can "
        "ignore this message and nothing will happen.\n"
    )
    send(to=to, subject="Confirm your DocuMind email address", body=body)
