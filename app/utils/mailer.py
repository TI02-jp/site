"""Utility helpers to deliver transactional emails via SMTP."""

from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from typing import Iterable, Sequence


class EmailDeliveryError(RuntimeError):
    """Raised when an email cannot be dispatched."""


def _coerce_recipients(recipients: Iterable[str | None]) -> list[str]:
    """Return a normalized list of recipient emails."""

    cleaned: list[str] = []
    for recipient in recipients:
        if not recipient:
            continue
        email = recipient.strip()
        if email:
            cleaned.append(email)
    return cleaned


def _build_plain_text(html_body: str) -> str:
    """Generate a plain-text alternative for an HTML message."""

    text = re.sub(r"<\s*br\s*/?>", "\n", html_body, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def send_email(
    *,
    subject: str,
    html_body: str,
    recipients: Sequence[str | None],
    sender: str | None = None,
    reply_to: str | None = None,
) -> None:
    """Dispatch an email message using SMTP settings from the environment."""

    to_addresses = _coerce_recipients(recipients)
    if not to_addresses:
        raise EmailDeliveryError("Nenhum destinatário válido informado.")

    host = os.getenv("SMTP_HOST")
    if not host:
        raise EmailDeliveryError("Configuração SMTP_HOST ausente.")

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    use_ssl = os.getenv("SMTP_USE_SSL", "0") == "1"
    use_tls = os.getenv("SMTP_USE_TLS", "1") != "0"

    mail_from = sender or os.getenv("SMTP_SENDER") or username
    if not mail_from:
        raise EmailDeliveryError("Remetente do e-mail não configurado.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail_from
    message["To"] = ", ".join(to_addresses)
    if reply_to:
        message["Reply-To"] = reply_to

    plain_text = _build_plain_text(html_body)
    if plain_text:
        message.set_content(plain_text)
        message.add_alternative(html_body, subtype="html")
    else:
        message.set_content(html_body, subtype="html")

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port) as server:
                if username and password:
                    server.login(username, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port) as server:
                server.ehlo()
                if use_tls:
                    server.starttls()
                if username and password:
                    server.login(username, password)
                server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError(str(exc)) from exc
