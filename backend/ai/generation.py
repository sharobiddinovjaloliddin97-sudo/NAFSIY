import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core import leases


GENERATION_TIMEOUT_SECONDS = 120
CANCELLATION_GRACE_SECONDS = 2
logger = logging.getLogger(__name__)


def session_call(bind, function, *args):
    try:
        with Session(bind=bind, autoflush=False) as db:
            return function(db, *args)
    except SQLAlchemyError:
        raise HTTPException(503, "AI database operation unavailable") from None


async def settle(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def database_call(function, *args, on_cancel=None):
    task = asyncio.create_task(run_in_threadpool(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            result = await settle(task)
            if on_cancel is not None:
                await settle(asyncio.create_task(run_in_threadpool(on_cancel, result)))
        except Exception as error:
            logger.error("Cancelled database phase: %s", type(error).__name__)
        raise


async def heartbeat(bind, lease):
    while True:
        await asyncio.sleep(leases.RENEW_INTERVAL_SECONDS)
        await database_call(session_call, bind, leases.renew_lease, lease)


def consume_result(task):
    if not task.cancelled():
        task.exception()


async def generate_with_lease(bind, lease, generate, *, question, history):
    await database_call(session_call, bind, leases.renew_lease, lease)
    model = asyncio.create_task(generate(question=question, history=history))
    renewal = asyncio.create_task(heartbeat(bind, lease))
    try:
        async with asyncio.timeout(GENERATION_TIMEOUT_SECONDS):
            finished, _ = await asyncio.wait({model, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if renewal in finished:
                await renewal
                raise HTTPException(503, "Generation lease renewal stopped")
            return await model
    except TimeoutError:
        raise HTTPException(504, "AI generation timed out; retry with the same Idempotency-Key") from None
    finally:
        renewal.cancel()
        try:
            await settle(renewal)
        except (asyncio.CancelledError, Exception):
            pass
        if not model.done():
            model.cancel()
            wait = asyncio.create_task(asyncio.wait({model}, timeout=CANCELLATION_GRACE_SECONDS))
            await settle(wait)
        if model.done():
            consume_result(model)
        else:
            model.add_done_callback(consume_result)
