"""Ghi/đọc tín hiệu quảng cáo đã chuẩn hoá."""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.ads import AdSignal

_UPDATABLE = (
    "request_id",
    "page_id",
    "page_name",
    "country",
    "start_date",
    "end_date",
    "is_active",
    "active_days",
    "reach",
    "impressions",
    "collation_count",
    "ad_copy",
    "cta_text",
    "media_type",
    "media_urls",
    "landing_page_url",
    "ad_library_url",
    "currency",
    "matched_query",
    "raw",
)


async def upsert_ads(db: AsyncSession, ads: Iterable[dict]) -> dict[str, int]:
    """Upsert theo (source, external_ad_id).

    Quét lại cùng từ khoá sẽ gặp lại phần lớn ads cũ — khi đó chỉ cập nhật
    `active_days` (tăng lên vì ads vẫn đang chạy) và `last_seen_at`, không chèn dòng mới.
    """
    # Gộp trùng trong cùng batch: Postgres từ chối ON CONFLICT DO UPDATE nếu một
    # câu lệnh đụng cùng một dòng hai lần, mà sàn hay trả lại cùng ad ở trang sau.
    deduped: dict[tuple[str, str], dict] = {}
    for a in ads:
        deduped[(a["source"], a["external_ad_id"])] = a

    rows = list(deduped.values())
    if not rows:
        return {"total": 0, "inserted": 0, "updated": 0}

    stmt = pg_insert(AdSignal).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ad_signal_natural",
        set_={
            **{c: stmt.excluded[c] for c in _UPDATABLE},
            "last_seen_at": func.now(),
        },
    ).returning(AdSignal.id, literal_column("(xmax = 0)").label("inserted"))

    result = await db.execute(stmt)
    outcome = result.all()
    await db.commit()

    inserted = sum(1 for r in outcome if r.inserted)
    return {"total": len(outcome), "inserted": inserted, "updated": len(outcome) - inserted}


async def list_ads(
    db: AsyncSession,
    *,
    country: str | None = None,
    page_id: str | None = None,
    min_active_days: int | None = None,
    only_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AdSignal]:
    """`min_active_days` chính là bộ lọc SRS Bước 2.1a yêu cầu:
    chỉ lấy chiến dịch chạy dài ngày. Trước đây không làm được vì dữ liệu nằm
    trong một ô JSONB."""
    q = select(AdSignal).order_by(AdSignal.active_days.desc().nullslast())
    if country:
        q = q.where(AdSignal.country == country)
    if page_id:
        q = q.where(AdSignal.page_id == page_id)
    if min_active_days is not None:
        q = q.where(AdSignal.active_days >= min_active_days)
    if only_active is not None:
        q = q.where(AdSignal.is_active.is_(only_active))
    result = await db.execute(q.limit(limit).offset(offset))
    return list(result.scalars().all())


async def landing_pages(db: AsyncSession, *, min_active_days: int = 14, limit: int = 200) -> list[dict]:
    """Landing page của những đối thủ chạy ads lâu nhất.

    Đây là mắt xích nối sang SRS Bước 3: giá cần so là giá của đối thủ **đang thắng**,
    không phải giá của một store bất kỳ tìm được trên mạng.
    """
    q = (
        select(
            AdSignal.landing_page_url,
            AdSignal.page_name,
            AdSignal.country,
            func.max(AdSignal.active_days).label("max_active_days"),
            func.count(AdSignal.id).label("ad_count"),
        )
        .where(AdSignal.landing_page_url.isnot(None), AdSignal.active_days >= min_active_days)
        .group_by(AdSignal.landing_page_url, AdSignal.page_name, AdSignal.country)
        .order_by(func.max(AdSignal.active_days).desc())
        .limit(limit)
    )
    rows = await db.execute(q)
    return [
        {
            "landing_page_url": r.landing_page_url,
            "page_name": r.page_name,
            "country": r.country,
            "max_active_days": r.max_active_days,
            "ad_count": r.ad_count,
        }
        for r in rows.all()
    ]


def to_dict(a: AdSignal) -> dict[str, Any]:
    return {
        "id": a.id,
        "source": a.source,
        "external_ad_id": a.external_ad_id,
        "page_id": a.page_id,
        "page_name": a.page_name,
        "country": a.country,
        "start_date": a.start_date.isoformat() if a.start_date else None,
        "end_date": a.end_date.isoformat() if a.end_date else None,
        "is_active": a.is_active,
        "active_days": a.active_days,
        "reach": a.reach,
        "impressions": a.impressions,
        "collation_count": a.collation_count,
        "ad_copy": a.ad_copy,
        "cta_text": a.cta_text,
        "media_type": a.media_type,
        "media_urls": a.media_urls,
        "landing_page_url": a.landing_page_url,
        "ad_library_url": a.ad_library_url,
        "matched_query": a.matched_query,
        "first_seen_at": a.first_seen_at.isoformat() if a.first_seen_at else None,
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
    }
