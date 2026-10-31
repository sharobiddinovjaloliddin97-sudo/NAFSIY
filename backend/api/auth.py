import hashlib
import hmac
import secrets
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.auth_tokens import COOKIE_NAME, TOKEN_TTL, JWT_ISSUER, JWT_AUDIENCE, decode_session, jwt_secret, security_key
from backend.core.csrf import (
    bootstrap_context, clear_context, csrf_failure, issue_token, require_login_csrf, require_session_csrf,
)
from backend.core.request_security import UNSAFE_METHODS, rate_ip, require_trusted_origin
from backend.core.auth_rate_limit import (
    BOOTSTRAP_LIMIT, BOOTSTRAP_WINDOW, login_admission, refund_successful_login,
    registration_admission, take_slot,
)
from backend.db.auth_models import User
from backend.db.database import get_db


password_hash = PasswordHash.recommended()
DUMMY_HASH = password_hash.hash(secrets.token_urlsafe(32))


router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Authentication"],
    dependencies=[Depends(require_trusted_origin)],
)


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class RegisterRequest(LoginRequest):
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    created_at: datetime
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


def set_auth_cookie(response: Response, user_id: str, secret: str, version: int = 0):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + TOKEN_TTL,
         "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "jti": secrets.token_hex(16), "ver": version},
        secret,
        algorithm="HS256",
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(TOKEN_TTL.total_seconds()),
        expires=now + TOKEN_TTL,
        path="/",
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    clear_context(response)


def get_current_user(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> User:
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated", headers={"Cache-Control": "no-store"})
    claims = decode_session(token)
    user_id = claims["sub"]
    try:
        user = db.get(User, user_id)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication unavailable", headers={"Cache-Control": "no-store"}) from None
    if user is None or not user.is_active or claims.get("ver", 0) != user.auth_version or (settings.REQUIRE_EMAIL_VERIFICATION and not user.email_verified):
        raise HTTPException(401, "Invalid or expired session", headers={"Cache-Control": "no-store"})
    if request.method in UNSAFE_METHODS:
        require_session_csrf(request, token)
    return user


class RegistrationResponse(BaseModel):
    message: str = "Registration request accepted. Check your email for the verification link before signing in."


@router.post("/register", response_model=RegistrationResponse, status_code=202, dependencies=[Depends(require_login_csrf)])
def register(data: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    jwt_secret()
    registration_admission(db, request, str(data.email))
    encoded = password_hash.hash(data.password)
    response.headers["Cache-Control"] = "no-store"
    try:
        dialect = db.get_bind().dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise HTTPException(503, "Authentication unavailable")
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        db.execute(insert(User).values(email=str(data.email), password_hash=encoded).on_conflict_do_nothing(
            index_elements=[User.email],
        ))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication unavailable") from None
    from backend.core.recovery import request_link
    request_link(db, str(data.email), "verify")
    return RegistrationResponse()


@router.post("/login", response_model=UserResponse, dependencies=[Depends(require_login_csrf)])
def login(data: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    secret = jwt_secret()
    pair_key = login_admission(db, request, str(data.email))
    try:
        user = db.scalar(select(User).where(User.email == data.email))
        encoded = user.password_hash if user else DUMMY_HASH
        user_id = user.id if user else None
        active = user.is_active if user else False
        initial_version = user.auth_version if user else None
        if user and settings.REQUIRE_EMAIL_VERIFICATION and not user.email_verified:
            active = False
        db.rollback()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication unavailable", headers={"Cache-Control": "no-store"}) from None
    try:
        valid, updated_hash = password_hash.verify_and_update(data.password, encoded)
    except UnknownHashError:
        password_hash.verify_and_update(data.password, DUMMY_HASH)
        valid, updated_hash = False, None
    if not valid or user_id is None or not active:
        raise HTTPException(401, "Invalid email or password", headers={"Cache-Control": "no-store"})
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active or user.auth_version != initial_version or user.password_hash != encoded:
            raise HTTPException(401, "Invalid email or password", headers={"Cache-Control": "no-store"})
        if updated_hash:
            changed = db.execute(update(User).where(
                User.id == user.id, User.auth_version == initial_version, User.password_hash == encoded,
            ).values(password_hash=updated_hash)).rowcount
            if changed != 1:
                db.rollback()
                raise HTTPException(401, "Invalid email or password")
            db.commit()
        refund_successful_login(db, pair_key)
        db.refresh(user)
        result = UserResponse.model_validate(user)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication unavailable", headers={"Cache-Control": "no-store"}) from None
    # Keep the version authenticated by password verification. A reset/logout
    # between that check and cookie issuance must invalidate this in-flight login.
    set_auth_cookie(response, user.id, secret, initial_version)
    return result


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout")
def logout(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        db.execute(update(User).where(User.id == user.id).values(auth_version=User.auth_version + 1))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication unavailable") from None
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    clear_context(response)
    return {"message": "Logged out"}


@router.get("/csrf")
def csrf_bootstrap(request: Request, response: Response, db: Session = Depends(get_db)):
    jwt_secret()
    require_trusted_origin(request, bootstrap=True)
    if request.headers.get("X-CSRF-Request") != "1":
        raise csrf_failure()
    take_slot(db, "bootstrap-ip", rate_ip(request), BOOTSTRAP_LIMIT, BOOTSTRAP_WINDOW)
    context, expiry = bootstrap_context(request, response)
    session_token = None
    session_scope = None
    raw = request.cookies.get(COOKIE_NAME)
    session_expiry = expiry
    if raw:
        try:
            claims = decode_session(raw)
        except HTTPException as error:
            if error.status_code != 401:
                raise
        else:
            try:
                user = db.get(User, claims["sub"])
            except SQLAlchemyError:
                db.rollback()
                raise HTTPException(503, "Authentication unavailable", headers={"Cache-Control": "no-store"}) from None
            if user is not None and user.is_active and claims.get("ver", 0) == user.auth_version and (not settings.REQUIRE_EMAIL_VERIFICATION or user.email_verified):
                session_expiry = int(claims["exp"])
                session_token = issue_token("session", raw, until=session_expiry)
                session_scope = hmac.new(security_key("csrf-ui-session"), raw.encode(), hashlib.sha256).hexdigest()
    response.headers.update({"Cache-Control": "no-store, private", "Pragma": "no-cache", "Vary": "Origin, Cookie"})
    anonymous = issue_token("anonymous", context, until=expiry)
    tokens = [token for token in (anonymous, session_token) if token]
    return {
        "csrf_token": session_token, "auth_csrf_token": anonymous,
        "session_scope": session_scope,
        "expires_at": min(int(token.split(".")[2]) for token in tokens),
    }


class RecoveryRequest(BaseModel):
    email: EmailStr = Field(max_length=320)


class RecoveryConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class ResetConfirm(RecoveryConfirm):
    password: str = Field(min_length=8, max_length=128)


@router.post("/recovery/{purpose}/request", status_code=202, dependencies=[Depends(require_login_csrf)])
def recovery_request(purpose: str, data: RecoveryRequest, request: Request,
                     response: Response, db: Session = Depends(get_db)):
    if purpose not in ("verify", "reset"):
        raise HTTPException(404, "Not found")
    from backend.core.recovery import request_link
    email = str(data.email).lower()
    take_slot(db, "recovery-ip", rate_ip(request), 10, 3600)
    take_slot(db, "recovery-pair", email + "\0" + rate_ip(request), 3, 3600)
    request_link(db, email, purpose)
    response.headers["Cache-Control"] = "no-store"
    return {"message": "If eligible, account instructions will be sent."}


@router.post("/recovery/verify/confirm", dependencies=[Depends(require_login_csrf)])
def verify_confirm(data: RecoveryConfirm, request: Request, response: Response, db: Session = Depends(get_db)):
    from backend.core.recovery import consume
    take_slot(db, "recovery-confirm", rate_ip(request), 20, 3600)
    consume(db, data.token, "verify")
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Email verified. Sign in to continue."}


@router.post("/recovery/reset/confirm", dependencies=[Depends(require_login_csrf)])
def reset_confirm(data: ResetConfirm, request: Request, response: Response, db: Session = Depends(get_db)):
    from backend.core.recovery import consume
    take_slot(db, "recovery-confirm", rate_ip(request), 20, 3600)
    consume(db, data.token, "reset", password_hash.hash(data.password))
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Password updated. Sign in again."}
