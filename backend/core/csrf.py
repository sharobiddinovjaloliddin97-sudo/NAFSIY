import hashlib
import hmac
import re
import secrets
import time

from fastapi import HTTPException, Request, Response

from backend.core.auth_tokens import jwt_secret, security_key
from backend.core.config import settings
from backend.core.request_security import require_trusted_origin


CONTEXT_COOKIE = "nafsiy_csrf_context"
TOKEN_SECONDS = 900
CONTEXT_SECONDS = 1800
FAILURE = "CSRF validation failed"
PATTERN = re.compile(r"v1\.(\d{1,12})\.(\d{1,12})\.([A-Za-z0-9_-]{43})\.([0-9a-f]{64})")


def csrf_failure():
    return HTTPException(403, FAILURE, headers={"Cache-Control": "no-store"})


def signature(mode: str, binding: str, payload: str) -> str:
    bound = hmac.new(security_key("csrf-binding"), binding.encode(), hashlib.sha256).digest()
    return hmac.new(security_key("csrf:" + mode), bound + b"\0" + payload.encode(), hashlib.sha256).hexdigest()


def issue_token(mode: str, binding: str, *, lifetime: int = TOKEN_SECONDS, until: int | None = None) -> str:
    now = int(time.time())
    expiry = min(now + lifetime, until) if until is not None else now + lifetime
    payload = f"v1.{now}.{expiry}.{secrets.token_urlsafe(32)}"
    return payload + "." + signature(mode, binding, payload)


def verify_token(token: str, mode: str, binding: str) -> int | None:
    if not isinstance(token, str) or len(token) > 256:
        return None
    match = PATTERN.fullmatch(token)
    if not match:
        return None
    issued, expiry = int(match[1]), int(match[2])
    now = int(time.time())
    maximum = CONTEXT_SECONDS if mode == "context" else TOKEN_SECONDS
    if issued > now + 5 or expiry <= now or expiry <= issued or expiry - issued > maximum:
        return None
    expected = signature(mode, binding, token.rsplit(".", 1)[0])
    return expiry if hmac.compare_digest(expected, match[4]) else None


def require_login_csrf(request: Request):
    jwt_secret()
    require_trusted_origin(request)
    context = request.cookies.get(CONTEXT_COOKIE, "")
    if not verify_token(context, "context", "anonymous-bootstrap"):
        raise csrf_failure()
    if not verify_token(request.headers.get("X-CSRF-Token", ""), "anonymous", context):
        raise csrf_failure()


def require_session_csrf(request: Request, token: str):
    require_trusted_origin(request)
    if not verify_token(request.headers.get("X-CSRF-Token", ""), "session", token):
        raise csrf_failure()


def bootstrap_context(request: Request, response: Response) -> tuple[str, int]:
    context = request.cookies.get(CONTEXT_COOKIE, "")
    expiry = verify_token(context, "context", "anonymous-bootstrap")
    if expiry is None:
        context = issue_token("context", "anonymous-bootstrap", lifetime=CONTEXT_SECONDS)
        expiry = int(context.split(".")[2])
        response.set_cookie(
            CONTEXT_COOKIE, context, max_age=CONTEXT_SECONDS, path="/api/v1/auth",
            httponly=True, secure=settings.AUTH_COOKIE_SECURE, samesite="lax",
        )
    return context, expiry


def clear_context(response: Response):
    response.delete_cookie(CONTEXT_COOKIE, path="/api/v1/auth", httponly=True,
                           secure=settings.AUTH_COOKIE_SECURE, samesite="lax")
