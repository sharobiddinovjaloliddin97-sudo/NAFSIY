import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.ai.agent import ask_nafsiy
from backend.ai.generation import database_call, generate_with_lease, session_call, settle
from backend.api.auth import get_current_user, require_trusted_origin
from backend.api.user_data import MAX_MESSAGES
from backend.core.idempotency import complete_request, release_failed_request, reserve_request
from backend.core.leases import GenerationLease, fence_lease, release_lease, transaction_timeouts
from backend.db.auth_models import User
from backend.db.database import get_db
from backend.db.user_data_models import ChatMessage, ChatSession


router = APIRouter(prefix="/api/v1/ai", tags=["NAFSIY AI"])
logger = logging.getLogger(__name__)


class SendRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    model_config = ConfigDict(extra="forbid")

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Message must contain at least two characters")
        return value


class SourceInfo(BaseModel):
    type: Literal["quran", "hadith"]
    reference: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=120)
    verified: Literal[False] = False


class ChatResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=20000)
    sources: list[SourceInfo] = Field(default_factory=list, max_length=20)
    model: str


def reserve_turn(db: Session, user_id: str, session_id: str, key: str, question: str) -> dict:
    try:
        transaction_timeouts(db)
        chat_id = db.scalar(select(ChatSession.id).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id,
        ).with_for_update())
        if chat_id is None:
            raise HTTPException(404, "Chat not found")
        reserved = reserve_request(db, user_id, session_id, key, question)
        if reserved["state"] == "replay":
            db.rollback()
            return reserved
        rows = db.execute(select(ChatMessage.role, ChatMessage.content)
                          .where(ChatMessage.session_id == session_id)
                          .order_by(ChatMessage.position.desc()).limit(12)).all()
        reserved["history"] = [{"role": row.role, "content": row.content[:4000]} for row in reversed(rows)]
        db.commit()
        return reserved
    except SQLAlchemyError as error:
        db.rollback()
        if getattr(getattr(error, "orig", None), "sqlstate", None) == "55P03":
            raise HTTPException(409, "Chat is busy; retry later", headers={"Retry-After": "1"}) from None
        raise HTTPException(503, "AI chat database unavailable") from None
    except HTTPException:
        db.rollback()
        raise


def save_turn(
    db: Session, user_id: str, session_id: str, question: str,
    result: ChatResponse, lease: GenerationLease,
):
    try:
        transaction_timeouts(db)
        chat = db.scalar(select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id,
        ).with_for_update())
        if chat is None:
            raise HTTPException(404, "Chat not found")
        fence_lease(db, lease)
        position = db.scalar(select(func.max(ChatMessage.position)).where(ChatMessage.session_id == session_id)) or 0
        first_user = db.scalar(select(ChatMessage.id).where(
            ChatMessage.session_id == session_id, ChatMessage.role == "user",
        ).limit(1))
        if first_user is None:
            chat.title = question[:120]
        chat.updated_at = datetime.now(timezone.utc)
        db.add_all([
            ChatMessage(session_id=session_id, role="user", content=question,
                        sources_json="[]", position=position + 1),
            ChatMessage(session_id=session_id, role="assistant", content=result.answer,
                        sources_json=json.dumps([item.model_dump() for item in result.sources]), position=position + 2),
        ])
        db.flush()
        overflow = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id)
                              .order_by(ChatMessage.position.desc()).offset(MAX_MESSAGES)).all()
        for message in overflow:
            db.delete(message)
        complete_request(db, lease, result.model_dump())
        release_lease(db, lease)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "AI messages could not be saved") from None
    except HTTPException:
        db.rollback()
        raise


@router.post("/chats/{session_id}/send", response_model=ChatResponse)
async def send_message(
    session_id: str,
    request: SendRequest,
    http_request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_trusted_origin(http_request)
    user_id = user.id
    bind = db.get_bind()
    await database_call(db.rollback)

    def cleanup(reserved):
        if reserved["state"] == "reserved":
            session_call(bind, release_failed_request, reserved["lease"])

    reserved = await database_call(
        session_call, bind, reserve_turn, user_id, session_id, idempotency_key, request.message,
        on_cancel=cleanup,
    )
    if reserved["state"] == "replay":
        try:
            return ChatResponse.model_validate(reserved["response"])
        except ValidationError:
            raise HTTPException(503, "Saved AI response is unavailable") from None
    lease = reserved["lease"]
    try:
        generated = await generate_with_lease(
            bind, lease, ask_nafsiy, question=request.message, history=reserved["history"],
        )
        try:
            result = ChatResponse.model_validate(generated)
        except ValidationError:
            raise HTTPException(502, "AI returned an invalid answer") from None
        await database_call(session_call, bind, save_turn, user_id, session_id, request.message, result, lease)
        return result
    except BaseException:
        try:
            await settle(asyncio.create_task(database_call(session_call, bind, release_failed_request, lease)))
        except Exception as error:
            logger.error("Failed AI request cleanup: %s", type(error).__name__)
        raise
