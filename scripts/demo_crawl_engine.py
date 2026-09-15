"""Chứng minh pipeline `app/crawl/engine.py` chạy đúng bằng adapter giả — cột mốc
G1 trong docs/DEV-Design-Crawl-Engine.md §11: "chạy hết pipeline bằng fake, có số
liệu tỉ lệ thành công giả lập" — trước khi tốn một đồng nào cho vendor thật.

Chạy:
    docker compose exec api python -m scripts.demo_crawl_engine
"""

from __future__ import annotations

import asyncio

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crawl import engine
from app.crawl.contracts import Capability, FetchRequest
from app.crud.crawl_ops import cost_by_run, success_rate
from app.database import AsyncSessionLocal
from app.models.crawl_ops import CrawlSourcePolicy


async def _run_source(db: AsyncSession, redis, source: str, query: str, calls: int) -> None:
    print(f"\n=== {source} ===")
    for i in range(calls):
        req = FetchRequest(
            source=source, capability=Capability.SEARCH_KEYWORD, params={"q": query, "call": i}
        )
        result = await engine.fetch(req, db, redis)
        print(
            f"  call {i + 1}: outcome={result.outcome.value:<15} tier={result.tier_used or '-':<20} "
            f"items={len(result.items)} cost=${result.cost_usd:.4f}"
        )
    stats = await success_rate(db, source)
    print(f"  → success_rate={stats['success_rate']}  by_outcome={stats['by_outcome']}")


async def _ensure_cache_policy(db: AsyncSession) -> None:
    existing = await db.scalar(
        select(CrawlSourcePolicy).where(CrawlSourcePolicy.source == "fake_demo_cached")
    )
    if existing is None:
        db.add(CrawlSourcePolicy(source="fake_demo_cached", cache_ttl_seconds=60, max_attempts=1))
        await db.commit()


async def _run_cache_demo(db: AsyncSession, redis) -> None:
    print("\n=== fake_demo_cached (idempotency cache) ===")
    await _ensure_cache_policy(db)
    req = FetchRequest(
        source="fake_demo_cached", capability=Capability.SEARCH_KEYWORD, params={"q": "giống hệt nhau"}
    )
    r1 = await engine.fetch(req, db, redis)
    r2 = await engine.fetch(req, db, redis)  # cùng params → cùng idempotency_key → cache hit
    print(f"  lần 1: outcome={r1.outcome.value} cost=${r1.cost_usd:.4f} stale={r1.stale}")
    print(
        f"  lần 2: outcome={r2.outcome.value} cost=${r2.cost_usd:.4f} stale={r2.stale}"
        "  (kỳ vọng cost=0, stale=True — không gọi lại adapter)"
    )


async def _run_with_run_id(db: AsyncSession, redis) -> None:
    """Mô phỏng levelup_be mở 1 lượt research (`run_id`) rồi gọi 2 nguồn khác nhau —
    kiểm chứng `crawl_attempts.run_id` gộp đúng chi phí về một lượt, dù khác source
    và khác `request_id` (§6: "Chi phí thật cho một lần research")."""
    run_id = "run_demo_sneaker_niche"
    print(f"\n=== mô phỏng 1 lượt research: run_id={run_id} ===")
    for source, query in (("fake_demo", "sneaker A"), ("fake_flaky_demo", "sneaker B")):
        req = FetchRequest(
            source=source, capability=Capability.SEARCH_KEYWORD, params={"q": query}, run_id=run_id
        )
        result = await engine.fetch(req, db, redis)
        print(f"  {source}: outcome={result.outcome.value} cost=${result.cost_usd:.4f}")

    summary = await cost_by_run(db, run_id)
    print(
        f"  → cost_by_run: total=${summary['total_cost_usd']:.4f}  "
        f"requests={summary['distinct_requests']}  by_source={summary['cost_by_source']}"
    )


async def main() -> None:
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        async with AsyncSessionLocal() as db:
            await _run_source(db, redis, "fake_demo", "bàn phím cơ", calls=3)
            await _run_source(db, redis, "fake_empty_demo", "sản phẩm không tồn tại", calls=1)
            await _run_source(db, redis, "fake_flaky_demo", "chuột không dây", calls=1)
            await _run_cache_demo(db, redis)
            await _run_with_run_id(db, redis)
    finally:
        await redis.aclose()

    print("\nG1 OK: pipeline chạy hết fallback tier + retry + cache + đo tỉ lệ thành công.")


if __name__ == "__main__":
    asyncio.run(main())
