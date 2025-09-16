"""Utility helpers to deliver email messages using SMTP settings."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Iterable

from flask import current_app


def _resolve_sender(explicit_sender: str | None) -> str | None:
    """Return the configured sender address or ``None`` when unavailable."""

    app = current_app._get_current_object()
    if explicit_sender:
        return explicit_sender
    sender = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")
    if sender:
        return sender
    app.logger.warning("Mail sender not configured; skipping outbound message.")
    return None


def send_email(subject: str, body: str, recipients: Iterable[str], *, sender: str | None = None) -> bool:
    """Send a plaintext email using the application's SMTP configuration."""

    app = current_app._get_current_object()
    recipient_list = [r for r in recipients if r]
    if not recipient_list:
        app.logger.debug("No recipients supplied for subject '%s'; skipping email.", subject)
        return False

    if app.config.get("MAIL_SUPPRESS_SEND"):
        app.logger.info(
            "Email delivery suppressed. Subject '%s' would be sent to %s.",
            subject,
            ", ".join(recipient_list),
        )
        return True

    mail_server = app.config.get("MAIL_SERVER")
    if not mail_server:
        app.logger.warning(
            "MAIL_SERVER is not configured; unable to send email for subject '%s'.",
            subject,
        )
        return False

    resolved_sender = _resolve_sender(sender)
    if not resolved_sender:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = resolved_sender
    message["To"] = ", ".join(recipient_list)

    reply_to = app.config.get("MAIL_REPLY_TO")
    if reply_to:
        message["Reply-To"] = reply_to

    message.set_content(body)

    mail_port = int(app.config.get("MAIL_PORT", 587) or 587)
    mail_username = app.config.get("MAIL_USERNAME")
    mail_password = app.config.get("MAIL_PASSWORD")
    use_ssl = bool(app.config.get("MAIL_USE_SSL"))
    use_tls = bool(app.config.get("MAIL_USE_TLS", True)) and not use_ssl

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(mail_server, mail_port) as smtp:
                if mail_username and mail_password:
                    smtp.login(mail_username, mail_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(mail_server, mail_port) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls()
                if mail_username and mail_password:
                    smtp.login(mail_username, mail_password)
                smtp.send_message(message)
    except Exception:  # pragma: no cover - logging path
        app.logger.exception(
            "Failed to send email with subject '%s' to %s.",
            subject,
            ", ".join(recipient_list),
        )
        return False

    return True
