import asyncio
from contextvars import ContextVar

from fastapi import HTTPException

from backend.ai.generation import database_call, session_call, settle
from backend.core import ai_operations
from backend.core.config import settings


provider_context = ContextVar("nafsiy_provider_context", default=None)


def input_bound(messages):
    return sum(len(item["content"].encode("utf-8")) + 128 for item in messages) + 256


async def completion(client, phase, **kwargs):
    bound = input_bound(kwargs["messages"])
    if bound > settings.AI_MAX_INPUT_TOKENS:
        raise HTTPException(413, "AI context exceeds input limit")
    context = provider_context.get()
    attempt_id = None
    if context is not None:
        bind, lease = context
        attempt_id = await database_call(session_call, bind, ai_operations.start_attempt,
                                         lease, phase, bound, kwargs["max_completion_tokens"])
    try:
        response = await asyncio.wait_for(client.chat.completions.create(**kwargs), timeout=40)
    except BaseException as error:
        ai_operations.event("provider_error", category="cancelled" if isinstance(error, asyncio.CancelledError)
                            else "timeout" if isinstance(error, TimeoutError) or type(error).__name__ == "APITimeoutError" else "provider")
        if attempt_id is not None:
            try:
                await settle(asyncio.create_task(database_call(session_call, bind, ai_operations.record_usage,
                             lease, attempt_id, "error", None, None)))
            except Exception:
                ai_operations.event("usage_reconciliation_failed")
        raise
    if attempt_id is not None:
        usage = getattr(response, "usage", None)
        await database_call(session_call, bind, ai_operations.record_usage, lease, attempt_id, "success",
                            getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
    return response
