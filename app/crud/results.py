from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.results import FacebookAdsResult, GoogleTrendsResult


# ── Google Trends ─────────────────────────────────────────────────────────────

async def save_trends_result(
    db: AsyncSession,
    *,
    request_id: str,
    keywords: list[str],
    geo: str,
    date_range: str,
    data_type: str,
    source: str,
    timeline_data: dict | None = None,
    related_queries: dict | None = None,
) -> GoogleTrendsResult:
    row = GoogleTrendsResult(
        request_id=request_id,
        keywords=keywords,
        geo=geo,
        date_range=date_range,
        data_type=data_type,
        source=source,
        timeline_data=timeline_data,
        related_queries=related_queries,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_trends_results(
    db: AsyncSession,
    *,
    keyword: str | None = None,
    geo: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[GoogleTrendsResult]:
    q = select(GoogleTrendsResult).order_by(GoogleTrendsResult.created_at.desc())
    if keyword:
        q = q.where(GoogleTrendsResult.keywords.contains([keyword]))
    if geo:
        q = q.where(GoogleTrendsResult.geo == geo)
    if source:
        q = q.where(GoogleTrendsResult.source == source)
    result = await db.execute(q.limit(limit).offset(offset))
    return list(result.scalars().all())


# ── Facebook Ads ──────────────────────────────────────────────────────────────

async def save_facebook_ads_result(
    db: AsyncSession,
    *,
    request_id: str,
    query: str | None,
    page_id: str | None,
    country: str,
    category: str,
    media_type: str,
    active_status: str,
    min_date: str | None,
    max_date: str | None,
    fetch_details: bool,
    ads_data: list[dict],
) -> FacebookAdsResult:
    row = FacebookAdsResult(
        request_id=request_id,
        query=query,
        page_id=page_id,
        country=country,
        category=category,
        media_type=media_type,
        active_status=active_status,
        min_date=min_date,
        max_date=max_date,
        fetch_details=fetch_details,
        total_ads_count=len(ads_data),
        ads_data=ads_data,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_facebook_ads_results(
    db: AsyncSession,
    *,
    query: str | None = None,
    page_id: str | None = None,
    country: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[FacebookAdsResult]:
    q = select(FacebookAdsResult).order_by(FacebookAdsResult.created_at.desc())
    if query:
        q = q.where(FacebookAdsResult.query.ilike(f"%{query}%"))
    if page_id:
        q = q.where(FacebookAdsResult.page_id == page_id)
    if country:
        q = q.where(FacebookAdsResult.country == country)
    result = await db.execute(q.limit(limit).offset(offset))
    return list(result.scalars().all())
