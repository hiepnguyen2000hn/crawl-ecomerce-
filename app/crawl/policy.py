"""Đọc `crawl_source_policies`, cache trong process — xem docs §5.

Không có row cho một nguồn KHÔNG phải lỗi cấu hình: nguồn mới toanh (hoặc adapter
giả dùng để demo/test) chạy được ngay bằng default an toàn, vận hành thêm policy
riêng sau khi có số liệu thật từ `crawl_attempts`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.crawl_ops import CrawlSourcePolicy

_CACHE_TTL_S = 30.0


@dataclass(frozen=True)
class Policy:
    source: str
    enabled: bool = True
    tier_chain: tuple[str, ...] = ()
    max_concurrency: int = 2
    min_delay_ms: int = 1500
    jitter_ms: int = 500
    max_attempts: int = 2
    identity_max_requests: int | None = None
    identity_max_age_s: int | None = None
    soft_block_cooldown_s: int = 1800
    respect_retry_after: bool = True
    daily_budget_usd: float | None = None
    cache_ttl_seconds: int = 0


def _default_policy(source: str) -> Policy:
    return Policy(
        source=source,
        min_delay_ms=settings.crawl_min_delay_ms,
        jitter_ms=settings.crawl_jitter_ms,
    )


_cache: dict[str, tuple[float, Policy]] = {}


async def policy_for(db: AsyncSession, source: str) -> Policy:
    cached = _cache.get(source)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_S:
        return cached[1]

    row = await db.scalar(select(CrawlSourcePolicy).where(CrawlSourcePolicy.source == source))
    policy = (
        _default_policy(source)
        if row is None
        else Policy(
            source=row.source,
            enabled=row.enabled,
            tier_chain=tuple(row.tier_chain or ()),
            max_concurrency=row.max_concurrency,
            min_delay_ms=row.min_delay_ms,
            jitter_ms=row.jitter_ms,
            max_attempts=row.max_attempts,
            identity_max_requests=row.identity_max_requests,
            identity_max_age_s=row.identity_max_age_s,
            soft_block_cooldown_s=row.soft_block_cooldown_s,
            respect_retry_after=row.respect_retry_after,
            daily_budget_usd=float(row.daily_budget_usd) if row.daily_budget_usd is not None else None,
            cache_ttl_seconds=row.cache_ttl_seconds,
        )
    )
    _cache[source] = (time.monotonic(), policy)
    return policy


def invalidate(source: str | None = None) -> None:
    """Xoá cache in-process. Gọi sau khi sửa policy qua API/admin để không phải chờ TTL."""
    if source is None:
        _cache.clear()
    else:
        _cache.pop(source, None)
