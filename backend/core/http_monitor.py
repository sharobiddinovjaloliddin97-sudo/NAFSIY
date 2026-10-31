"""Aggregate HTTP telemetry with fixed field allowlist; no URL, identity or bodies."""
import json
import logging
import time

logger = logging.getLogger("nafsiy.http")


class HTTPMonitor:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.monotonic()
        status = 500
        async def observed(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
        try:
            await self.app(scope, receive, observed)
        finally:
            logger.info(json.dumps({"event": "http_request", "status": status,
                                   "duration_ms": round((time.monotonic() - start)*1000),
                                   "method": scope["method"] if scope["method"] in ("GET","POST","DELETE","OPTIONS") else "OTHER"}))
