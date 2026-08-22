from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import Request

SEARCH_LIMIT = 10
SEARCH_WINDOW = timedelta(minutes=1)

search_attempts: dict[str, list[datetime]] = {}
search_rate_limit_lock = asyncio.Lock()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    if request.client:
        return request.client.host
    
    return "unknown"

async def search_retry_after_seconds(request: Request) -> int:
    async with search_rate_limit_lock:
        now = datetime.now(timezone.utc)
        ip = client_ip(request)

        attempts = search_attempts.setdefault(ip, [])
        cutoff = now - SEARCH_WINDOW
        attempts[:] = [
            attempt
            for attempt in attempts
            if attempt > cutoff
        ]

        if len(attempts) >= SEARCH_LIMIT:
            seconds_left = SEARCH_WINDOW - (now - attempts[0])
            return max(1, int(seconds_left.total_seconds()))

        attempts.append(now)
        return 0


async def search_rate_limited(request: Request) -> bool:
    return await search_retry_after_seconds(request) > 0
