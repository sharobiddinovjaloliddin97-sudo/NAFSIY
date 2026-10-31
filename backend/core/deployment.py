"""Fail-closed staging settings, before accepting requests. Never runs DDL."""
from urllib.parse import urlsplit
from sqlalchemy.engine import make_url


def validate_staging(settings):
    if settings.APP_ENV == "development":
        return
    if settings.APP_ENV != "staging":
        raise ValueError("Only development or explicitly configured staging is supported")
    origin = urlsplit(settings.PUBLIC_WEB_URL)
    # Accessing port validates malformed/non-numeric/out-of-range values too.
    origin.port
    if origin.scheme != "https" or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username is not None:
        raise ValueError("Staging requires an exact HTTPS frontend origin")
    if not settings.AUTH_TRUSTED_ORIGINS or settings.PUBLIC_WEB_URL not in settings.AUTH_TRUSTED_ORIGINS:
        raise ValueError("PUBLIC_WEB_URL must be included in AUTH_TRUSTED_ORIGINS")
    if settings.CORS_ORIGINS != settings.AUTH_TRUSTED_ORIGINS:
        raise ValueError("CORS_ORIGINS must match AUTH_TRUSTED_ORIGINS")
    for item in settings.AUTH_TRUSTED_ORIGINS:
        o = urlsplit(item)
        o.port
        if o.scheme != "https" or not o.hostname or o.path or o.query or o.fragment or o.username is not None:
            raise ValueError(f"Origin {item} is not a valid HTTPS origin")
    if not settings.AUTH_COOKIE_SECURE or len(settings.JWT_SECRET_KEY) < 48:
        raise ValueError("Staging requires Secure cookies and a random signing key")
    if settings.ACCOUNT_DELETION_ENABLED:
        raise ValueError("Account deletion remains a release blocker")
    if not settings.REQUIRE_EMAIL_VERIFICATION or settings.EMAIL_MODE not in ("smtp", "resend"):
        raise ValueError("Staging requires verified accounts and configured email delivery")
    if settings.EMAIL_MODE == "smtp":
        if not all((settings.SMTP_HOST, settings.SMTP_USERNAME, settings.SMTP_PASSWORD, settings.EMAIL_FROM)):
            raise ValueError("Staging SMTP provider configuration is incomplete")
    elif settings.EMAIL_MODE == "resend":
        if not all((settings.RESEND_API_KEY, settings.EMAIL_FROM)):
            raise ValueError("Staging Resend provider configuration is incomplete")
        if settings.EMAIL_FROM.lower().endswith("@resend.dev"):
            raise ValueError("Staging requires a verified custom sender domain; onboarding@resend.dev is restricted to the account owner")
    if not settings.GROQ_API_KEY:
        raise ValueError("Staging AI provider configuration is incomplete")
    if settings.STAGING_PROXY_SECRET and len(settings.STAGING_PROXY_SECRET) < 32:
        raise ValueError("Staging proxy secret must be at least 32 characters")
    url = make_url(settings.DATABASE_URL)
    if url.get_backend_name() != "postgresql" or url.query.get("sslmode") not in ("require", "verify-full"):
        raise ValueError("Staging requires TLS PostgreSQL")
    if settings.MAX_GLOBAL_AI_GENERATIONS > 2:
        raise ValueError("Staging capacity must remain at most two until load approval")
