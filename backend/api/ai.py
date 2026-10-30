import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.ai.agent import ask_nafsiy
from backend.api.auth import get_current_user, require_trusted_origin
from backend.api.user_data import MAX_MESSAGES
from backend.core.rate_limit import check_ai_rate_limit
from backend.db.auth_models import User
from backend.db.database import get_db
from backend.db.user_data_models import ChatMessage, ChatSession


router = APIRouter(prefix="/api/v1/ai", tags=["NAFSIY AI"])


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


def prepare_turn(db: Session, user_id: str, session_id: str) -> list[dict]:
    try:
        chat_id = db.scalar(select(ChatSession.id).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id,
        ))
        if chat_id is None:
            raise HTTPException(404, "Chat not found")
        check_ai_rate_limit(db, user_id)
        rows = db.execute(select(ChatMessage.role, ChatMessage.content)
                          .where(ChatMessage.session_id == session_id)
                          .order_by(ChatMessage.position.desc()).limit(12)).all()
        history = [{"role": row.role, "content": row.content[:4000]} for row in reversed(rows)]
        db.rollback()
        return history
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "AI chat database unavailable") from None
    except HTTPException:
        db.rollback()
        raise


def save_turn(db: Session, user_id: str, session_id: str, question: str, result: ChatResponse):
    try:
        chat = db.scalar(select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id,
        ).with_for_update())
        if chat is None:
            raise HTTPException(404, "Chat not found")
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_trusted_origin(http_request)
    user_id = user.id
    history = await run_in_threadpool(prepare_turn, db, user_id, session_id)
    generated = await ask_nafsiy(question=request.message, history=history)
    try:
        result = ChatResponse.model_validate(generated)
    except ValidationError:
        raise HTTPException(502, "AI returned an invalid answer") from None
    await run_in_threadpool(save_turn, db, user_id, session_id, request.message, result)
    return result
