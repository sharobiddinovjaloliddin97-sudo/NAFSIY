import asyncio

from backend.ai.generation import database_call, session_call
from backend.core.ai_operations import event, maintain, snapshot
from backend.db.monitor import pool_status


INTERVAL_SECONDS = 60


async def cleanup_loop(bind):
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        try:
            await database_call(session_call, bind, maintain)
            values = await database_call(session_call, bind, snapshot)
            event("operations_health", **values)
            event("database_pool_health", **pool_status(bind))
        except Exception as error:
            event("cleanup_failed", category=type(error).__name__)
