"""
core/mail.py — Sending one message, either to a log or to Gmail.

Two transports behind one function because the same code has to work in three places with very different
needs. A clone of this repo has no SMTP credentials, so `console` writes the verification link to the log
and a reviewer can confirm an address without configuring anything. CI needs the same thing without
needing a mail server. And the deployed demo uses `smtp`, which is the only transport that can actually
deliver to a stranger's inbox.

Gmail specifics, because they are the reason this is not just `smtplib.SMTP(...)`: port 465 is implicit
TLS, the `SMTP_SSL` class rather than `starttls`, and the password is an **App Password** from
myaccount.google.com/apppasswords, never the account password — Google disabled raw account-password
auth for this endpoint, and an App Password can be revoked on its own.

Failures are reported by exception *class*, never message. A server reply to a failed AUTH command can
echo the credential in its text, and this module is the one place in the app that holds a password.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings

logger = logging.getLogger("documind.mail")

# Bounded so a SMTP host that accepts the TCP connection and then says nothing cannot hold a worker
# thread open indefinitely — Render's free instance runs one process, and every hung send is a request
# that will not be served.
SMTP_TIMEOUT_SECONDS = 10


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

    _send_smtp(to=to, subject=subject, body=body)


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
