import asyncio
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import ProviderKey

COOLDOWN_MINUTES = 10


class KeyPool:
    """
    Round-robin pool of SerpAPI keys.
    On 429 → mark key on cooldown for COOLDOWN_MINUTES.
    Thread-safe via asyncio.Lock.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._index = 0

    async def get_available(self, db: AsyncSession) -> Optional[ProviderKey]:
        async with self._lock:
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(ProviderKey)
                .where(ProviderKey.is_active == True)
                .where(
                    (ProviderKey.cooldown_until == None)
                    | (ProviderKey.cooldown_until <= now)
                )
                .order_by(ProviderKey.usage_count.asc())
            )
            keys = list(result.scalars().all())
            if not keys:
                return None
            # Round-robin among available keys
            key = keys[self._index % len(keys)]
            self._index = (self._index + 1) % len(keys)
            return key

    async def mark_rate_limited(self, db: AsyncSession, key_id: int) -> None:
        from datetime import timedelta
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
        await db.execute(
            update(ProviderKey)
            .where(ProviderKey.id == key_id)
            .values(cooldown_until=cooldown_until, error_count=ProviderKey.error_count + 1)
        )
        await db.commit()

    async def mark_used(self, db: AsyncSession, key_id: int) -> None:
        await db.execute(
            update(ProviderKey)
            .where(ProviderKey.id == key_id)
            .values(usage_count=ProviderKey.usage_count + 1, cooldown_until=None)
        )
        await db.commit()

    async def mark_error(self, db: AsyncSession, key_id: int) -> None:
        await db.execute(
            update(ProviderKey)
            .where(ProviderKey.id == key_id)
            .values(error_count=ProviderKey.error_count + 1)
        )
        await db.commit()


key_pool = KeyPool()
