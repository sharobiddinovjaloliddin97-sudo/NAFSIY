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


class ResendDelivery:
    def send(self, recipient: str, purpose: str, token: str) -> None:
        import httpx
        if not settings.RESEND_API_KEY or not settings.EMAIL_FROM:
            raise ValueError("Resend email delivery is not configured")
        if settings.APP_ENV == "staging" and settings.EMAIL_FROM.lower().endswith("@resend.dev"):
            raise ValueError("onboarding@resend.dev is restricted to the account owner and cannot send to arbitrary recipients")
        payload = {
            "from": settings.EMAIL_FROM,
            "to": [recipient],
            "subject": "NAFSIY account security",
            "text": (
                f"Open {settings.PUBLIC_WEB_URL}/account-recovery#{purpose}:{token}\n"
                "If you did not request this, ignore this message."
            ),
        }
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json=payload,
            )
            response.raise_for_status()


def delivery() -> Delivery:
    if settings.EMAIL_MODE == "disabled":
        return DisabledDelivery()
    if settings.EMAIL_MODE == "smtp":
        return SMTPDelivery()
    if settings.EMAIL_MODE == "resend":
        return ResendDelivery()
    raise ValueError("Unsupported email mode")
