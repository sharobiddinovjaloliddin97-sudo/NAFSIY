import json
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from backend.api.auth import get_current_user, require_trusted_origin
from backend.db.auth_models import User
from backend.db.database import get_db
from backend.db.user_data_models import Bookmark, ChatMessage, ChatSession
from backend.core.config import settings


MAX_SESSIONS = 50
MAX_MESSAGES = 100
router = APIRouter(
    prefix="/api/v1/user", tags=["User Data"],
    dependencies=[Depends(require_trusted_origin)],
)


class ChatCreate(BaseModel):
    title: str = Field(default="Yangi suhbat", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title cannot be blank")
        return value.strip()


from backend.ai.source_metadata import SourceInfo


class StoredMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)
    sources: list[SourceInfo] = Field(default_factory=list, max_length=20)

    @field_validator("content")
    @classmethod
    def nonempty_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value


class ChatResponse(BaseModel):
    id: str
    title: str
    createdAt: datetime
    updatedAt: datetime
    messages: list[StoredMessage]


class BookmarkCreate(BaseModel):
    kind: Literal["quran", "hadith"]
    reference: str = Field(min_length=3, max_length=120)
    title: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def valid_reference(self):
        if self.kind == "quran":
            match = re.fullmatch(r"([1-9]\d{0,2}):([1-9]\d{0,2})", self.reference)
            valid = match and int(match[1]) <= 114 and int(match[2]) <= 286
        else:
            match = re.fullmatch(r"([a-z0-9-]{2,50}):([1-9]\d{0,5})", self.reference)
            valid = match and int(match[2]) <= 100000
        if not valid or not self.title.strip():
            raise ValueError("Invalid bookmark reference or title")
        return self


class BookmarkResponse(BookmarkCreate):
    id: str
    createdAt: datetime


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def chat_response(chat: ChatSession) -> ChatResponse:
    return ChatResponse(
        id=chat.id, title=chat.title, createdAt=utc(chat.created_at), updatedAt=utc(chat.updated_at),
        messages=[StoredMessage(role=message.role, content=message.content,
                                sources=json.loads(message.sources_json)) for message in chat.messages],
    )


def bookmark_response(bookmark: Bookmark) -> BookmarkResponse:
    return BookmarkResponse(
        id=bookmark.id, kind=bookmark.kind, reference=bookmark.reference,
        title=bookmark.title, createdAt=utc(bookmark.created_at),
    )


def owned_chat(db: Session, user: User, chat_id: str, lock=False) -> ChatSession:
    query = select(ChatSession).where(ChatSession.id == chat_id, ChatSession.user_id == user.id)
    if lock:
        query = query.with_for_update()
    chat = db.scalar(query)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    return chat


class AccountDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["DELETE MY ACCOUNT"]
    password: str = Field(min_length=1, max_length=128)


@router.delete("/account", status_code=204)
def delete_account(data: AccountDelete, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from backend.api.auth import password_hash, COOKIE_NAME
    from backend.core.account_deletion import delete_account_data
    from backend.core.auth_rate_limit import take_slot
    from backend.core.csrf import clear_context
    from pwdlib.exceptions import UnknownHashError
    if not settings.ACCOUNT_DELETION_ENABLED:
        raise HTTPException(503, "Account deletion is not enabled")
    user_id, encoded = user.id, user.password_hash
    db.rollback()
    take_slot(db, "account-delete", user_id, 3, 300)
    try:
        valid = password_hash.verify(data.password, encoded)
    except UnknownHashError:
        valid = False
    if not valid:
        raise HTTPException(401, "Reauthentication failed")
    try:
        delete_account_data(db, user_id, expected_password_hash=encoded)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Account deletion unavailable; retry later") from None
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.AUTH_COOKIE_SECURE, samesite="lax")
    clear_context(response)
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    return response


@router.get("/chats", response_model=list[ChatResponse])
def list_chats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chats = db.scalars(select(ChatSession).where(ChatSession.user_id == user.id)
                       .options(selectinload(ChatSession.messages))
                       .order_by(ChatSession.updated_at.desc(), ChatSession.id).limit(MAX_SESSIONS))
    return [chat_response(chat) for chat in chats]


@router.post("/chats", response_model=ChatResponse, status_code=201)
def create_chat(data: ChatCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    count = db.scalar(select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user.id))
    if count >= MAX_SESSIONS:
        raise HTTPException(409, "Chat limit reached; delete an existing chat first")
    chat = ChatSession(user_id=user.id, title=data.title)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat_response(chat)


@router.get("/chats/{chat_id}", response_model=ChatResponse)
def get_chat(chat_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return chat_response(owned_chat(db, user, chat_id))


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = owned_chat(db, user, chat_id, lock=True)
    db.delete(chat)
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/bookmarks", response_model=list[BookmarkResponse])
def list_bookmarks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Bookmark).where(Bookmark.user_id == user.id)
                       .order_by(Bookmark.created_at.desc(), Bookmark.id))
    return [bookmark_response(item) for item in items]


@router.post("/bookmarks", response_model=BookmarkResponse, status_code=201)
def create_bookmark(data: BookmarkCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(Bookmark).where(Bookmark.user_id == user.id, Bookmark.kind == data.kind,
                                   Bookmark.reference == data.reference)
    existing = db.scalar(query)
    if existing:
        return bookmark_response(existing)
    bookmark = Bookmark(user_id=user.id, **data.model_dump())
    db.add(bookmark)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(query)
        if existing:
            return bookmark_response(existing)
        raise
    db.refresh(bookmark)
    return bookmark_response(bookmark)


@router.delete("/bookmarks/{bookmark_id}", status_code=204)
def delete_bookmark(bookmark_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bookmark = db.scalar(select(Bookmark).where(Bookmark.id == bookmark_id, Bookmark.user_id == user.id))
    if bookmark is None:
        raise HTTPException(404, "Bookmark not found")
    db.delete(bookmark)
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
