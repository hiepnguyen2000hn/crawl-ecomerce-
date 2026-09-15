from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.browser_profile import BrowserProfile
from app.services.cloak_browser import generate_seed


async def create_profile(
    db: AsyncSession,
    label: str,
    proxy_url: str | None = None,
    profile_dir: str | None = None,
    notes: str | None = None,
    fingerprint_seed: int | None = None,
) -> BrowserProfile:
    profile = BrowserProfile(
        label=label,
        fingerprint_seed=fingerprint_seed or generate_seed(),
        proxy_url=proxy_url,
        profile_dir=profile_dir,
        notes=notes,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def list_profiles(
    db: AsyncSession,
    active_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[BrowserProfile]:
    q = select(BrowserProfile)
    if active_only:
        q = q.where(BrowserProfile.is_active == True)
    q = q.order_by(BrowserProfile.id).limit(limit).offset(offset)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_profile(db: AsyncSession, profile_id: int) -> BrowserProfile | None:
    result = await db.execute(
        select(BrowserProfile).where(BrowserProfile.id == profile_id)
    )
    return result.scalar_one_or_none()


async def update_profile(
    db: AsyncSession,
    profile_id: int,
    label: str | None = None,
    proxy_url: str | None = None,
    profile_dir: str | None = None,
    is_active: bool | None = None,
    notes: str | None = None,
) -> BrowserProfile | None:
    values: dict = {}
    if label is not None:
        values["label"] = label
    if proxy_url is not None:
        values["proxy_url"] = proxy_url
    if profile_dir is not None:
        values["profile_dir"] = profile_dir
    if is_active is not None:
        values["is_active"] = is_active
    if notes is not None:
        values["notes"] = notes
    if not values:
        return await get_profile(db, profile_id)

    await db.execute(
        update(BrowserProfile).where(BrowserProfile.id == profile_id).values(**values)
    )
    await db.commit()
    return await get_profile(db, profile_id)


async def mark_used(db: AsyncSession, profile_id: int) -> None:
    await db.execute(
        update(BrowserProfile)
        .where(BrowserProfile.id == profile_id)
        .values(last_used_at=datetime.now(timezone.utc))
    )
    await db.commit()


async def mark_error(db: AsyncSession, profile_id: int) -> None:
    await db.execute(
        update(BrowserProfile)
        .where(BrowserProfile.id == profile_id)
        .values(error_count=BrowserProfile.error_count + 1)
    )
    await db.commit()


async def delete_profile(db: AsyncSession, profile_id: int) -> bool:
    profile = await get_profile(db, profile_id)
    if not profile:
        return False
    await db.delete(profile)
    await db.commit()
    return True
