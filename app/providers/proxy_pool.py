import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Proxy

PROXY_FAIL_MINUTES = 5


class ProxyPool:
    """
    Round-robin pool of proxies.
    On network error → mark proxy failed for PROXY_FAIL_MINUTES.
    Returns None if no proxies configured (direct connection fallback).
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._index = 0

    async def get_available(self, db: AsyncSession) -> Optional[Proxy]:
        async with self._lock:
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(Proxy)
                .where(Proxy.is_active == True)
                .where(
                    (Proxy.failed_until == None)
                    | (Proxy.failed_until <= now)
                )
                .order_by(Proxy.usage_count.asc())
            )
            proxies = list(result.scalars().all())
            if not proxies:
                return None
            proxy = proxies[self._index % len(proxies)]
            self._index = (self._index + 1) % len(proxies)
            return proxy

    async def mark_failed(self, db: AsyncSession, proxy_id: int) -> None:
        failed_until = datetime.now(timezone.utc) + timedelta(minutes=PROXY_FAIL_MINUTES)
        await db.execute(
            update(Proxy)
            .where(Proxy.id == proxy_id)
            .values(failed_until=failed_until, error_count=Proxy.error_count + 1)
        )
        await db.commit()

    async def mark_used(self, db: AsyncSession, proxy_id: int) -> None:
        await db.execute(
            update(Proxy)
            .where(Proxy.id == proxy_id)
            .values(usage_count=Proxy.usage_count + 1, failed_until=None)
        )
        await db.commit()


proxy_pool = ProxyPool()
