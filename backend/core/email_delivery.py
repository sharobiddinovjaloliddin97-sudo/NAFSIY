"""Delivery adapter. Disabled mode discards mail without logging tokens or addresses."""
from email.message import EmailMessage
import smtplib
import ssl
from typing import Protocol

from backend.core.config import settings


class Delivery(Protocol):
    def send(self, recipient: str, purpose: str, token: str) -> None: ...


class DisabledDelivery:
    def send(self, recipient: str, purpose: str, token: str) -> None:
        pass


class SMTPDelivery:
    def send(self, recipient: str, purpose: str, token: str) -> None:
        message = EmailMessage()
        message["From"] = settings.EMAIL_FROM
        message["To"] = recipient
        message["Subject"] = "NAFSIY account security"
        # Fragment is never sent in HTTP request paths, access logs or referrers.
        message.set_content(f"Open {settings.PUBLIC_WEB_URL}/account-recovery#{purpose}:{token}\n"
                            "If you did not request this, ignore this message.")
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)


def delivery() -> Delivery:
    if settings.EMAIL_MODE == "disabled":
        return DisabledDelivery()
    if settings.EMAIL_MODE == "smtp":
        return SMTPDelivery()
    raise ValueError("Unsupported email mode")
