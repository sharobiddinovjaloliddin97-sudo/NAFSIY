import asyncio
import logging

from backend.ai.generation import database_call, session_call
from backend.core.auth_rate_limit import CLEANUP_BATCH, purge_expired


INTERVAL_SECONDS = 60
MAX_BATCHES = 10
logger = logging.getLogger(__name__)


async def cleanup_loop(bind):
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        try:
            for _ in range(MAX_BATCHES):
                removed = await database_call(session_call, bind, purge_expired)
                if removed < CLEANUP_BATCH:
                    break
        except Exception as error:
            logger.warning("Auth limiter maintenance unavailable: %s", type(error).__name__)
