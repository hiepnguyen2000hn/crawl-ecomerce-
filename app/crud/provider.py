from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Proxy, ProviderKey


async def add_key(db: AsyncSession, label: str, api_key: str) -> ProviderKey:
    entry = ProviderKey(label=label, api_key=api_key)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_keys(db: AsyncSession) -> list[ProviderKey]:
    result = await db.execute(select(ProviderKey).order_by(ProviderKey.id))
    return list(result.scalars().all())


async def toggle_key(db: AsyncSession, key_id: int, is_active: bool) -> ProviderKey | None:
    result = await db.execute(select(ProviderKey).where(ProviderKey.id == key_id))
    entry = result.scalar_one_or_none()
    if entry:
        entry.is_active = is_active
        await db.commit()
        await db.refresh(entry)
    return entry


async def delete_key(db: AsyncSession, key_id: int) -> bool:
    result = await db.execute(select(ProviderKey).where(ProviderKey.id == key_id))
    entry = result.scalar_one_or_none()
    if entry:
        await db.delete(entry)
        await db.commit()
        return True
    return False


async def add_proxy(db: AsyncSession, label: str, url: str) -> Proxy:
    entry = Proxy(label=label, url=url)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_proxies(db: AsyncSession) -> list[Proxy]:
    result = await db.execute(select(Proxy).order_by(Proxy.id))
    return list(result.scalars().all())


async def toggle_proxy(db: AsyncSession, proxy_id: int, is_active: bool) -> Proxy | None:
    result = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    entry = result.scalar_one_or_none()
    if entry:
        entry.is_active = is_active
        await db.commit()
        await db.refresh(entry)
    return entry


async def delete_proxy(db: AsyncSession, proxy_id: int) -> bool:
    result = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    entry = result.scalar_one_or_none()
    if entry:
        await db.delete(entry)
        await db.commit()
        return True
    return False
