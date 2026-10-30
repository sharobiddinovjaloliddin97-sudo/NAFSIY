import hashlib
import hmac
import re
from datetime import timedelta

import jwt
from fastapi import HTTPException
from jwt.exceptions import InvalidTokenError

from backend.core.config import settings


COOKIE_NAME = "nafsiy_access_token"
TOKEN_TTL = timedelta(hours=1)
JWT_ISSUER = "nafsiy"
JWT_AUDIENCE = "nafsiy-web"


def jwt_secret() -> str:
    if len(settings.JWT_SECRET_KEY) < 32:
        raise HTTPException(503, "Authentication security is not configured", headers={"Cache-Control": "no-store"})
    return settings.JWT_SECRET_KEY


def security_key(purpose: str) -> bytes:
    return hmac.new(jwt_secret().encode(), b"nafsiy-security-v1:" + purpose.encode(), hashlib.sha256).digest()


def decode_session(token: str) -> dict:
    try:
        claims = jwt.decode(
            token, jwt_secret(), algorithms=["HS256"], issuer=JWT_ISSUER, audience=JWT_AUDIENCE,
            options={"require": ["sub", "iat", "exp", "iss", "aud", "jti"]},
        )
        if not isinstance(claims["sub"], str) or len(claims["sub"]) != 32:
            raise InvalidTokenError()
        if not isinstance(claims["jti"], str) or not re.fullmatch(r"[0-9a-f]{32}", claims["jti"]):
            raise InvalidTokenError()
        return claims
    except InvalidTokenError:
        raise HTTPException(401, "Invalid or expired session", headers={"Cache-Control": "no-store"}) from None
