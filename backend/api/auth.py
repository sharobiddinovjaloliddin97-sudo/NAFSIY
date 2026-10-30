import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.auth_models import User
from backend.db.database import get_db


COOKIE_NAME = "nafsiy_access_token"
TOKEN_TTL = timedelta(hours=1)
JWT_ISSUER = "nafsiy"
JWT_AUDIENCE = "nafsiy-web"
TRUSTED_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}
password_hash = PasswordHash.recommended()
DUMMY_HASH = password_hash.hash(secrets.token_urlsafe(32))


def require_trusted_origin(request: Request):
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    origin = request.headers.get("origin")
    same_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin not in TRUSTED_ORIGINS and origin != same_origin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Untrusted request origin")


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


def jwt_secret() -> str:
    if len(settings.JWT_SECRET_KEY) < 32:
        raise HTTPException(503, "JWT_SECRET_KEY must contain at least 32 characters")
    return settings.JWT_SECRET_KEY


def set_auth_cookie(response: Response, user_id: str, secret: str):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + TOKEN_TTL,
         "iss": JWT_ISSUER, "aud": JWT_AUDIENCE},
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


def get_current_user(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> User:
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    secret = jwt_secret()
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
        user_id = claims["sub"]
        if not isinstance(user_id, str) or len(user_id) != 32:
            raise InvalidTokenError()
    except InvalidTokenError:
        raise HTTPException(401, "Invalid or expired session") from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Invalid or expired session")
    return user


@router.post("/register", response_model=UserResponse, status_code=201)
def register(data: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    secret = jwt_secret()
    if db.scalar(select(User.id).where(User.email == data.email)):
        raise HTTPException(409, "Email is already registered")
    user = User(email=str(data.email), password_hash=password_hash.hash(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email is already registered") from None
    db.refresh(user)
    set_auth_cookie(response, user.id, secret)
    return user


@router.post("/login", response_model=UserResponse)
def login(data: LoginRequest, response: Response, db: Session = Depends(get_db)):
    secret = jwt_secret()
    user = db.scalar(select(User).where(User.email == data.email))
    valid, updated_hash = password_hash.verify_and_update(
        data.password, user.password_hash if user else DUMMY_HASH,
    )
    if not valid or user is None or not user.is_active:
        raise HTTPException(401, "Invalid email or password")
    if updated_hash:
        user.password_hash = updated_hash
        db.commit()
        db.refresh(user)
    set_auth_cookie(response, user.id, secret)
    return user


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Logged out"}
