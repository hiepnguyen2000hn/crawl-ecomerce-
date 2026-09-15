from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.contracts import FetchRequest, FetchResult
from app.crawl.outcomes import Outcome
from app.models.crawl_ops import CrawlAttempt


async def record_attempt(
    db: AsyncSession,
    *,
    request_id: str,
    req: FetchRequest,
    tier: str,
    attempt_no: int,
    outcome: Outcome,
    result: FetchResult,
) -> CrawlAttempt:
    row = CrawlAttempt(
        request_id=request_id,
        run_id=req.run_id,
        source=req.source,
        capability=req.capability.value,
        tier=tier,
        identity_id=None,
        attempt_no=attempt_no,
        outcome=outcome.value,
        http_status=result.http_status,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        error_detail=result.error,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def spent_today_usd(db: AsyncSession, source: str) -> float:
    """Tổng `cost_usd` của `source` từ 00:00 UTC hôm nay — dùng để chặn trước khi
    vào tier đầu tiên nếu đã chạm `daily_budget_usd` (§5, §6)."""
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(
        select(func.coalesce(func.sum(CrawlAttempt.cost_usd), 0)).where(
            CrawlAttempt.source == source, CrawlAttempt.created_at >= start_of_day
        )
    )
    return float(total or 0)


async def cost_by_run(db: AsyncSession, run_id: str) -> dict:
    """Tổng chi phí + số request của một lượt research — trả lời câu "lượt nghiên
    cứu này tốn bao nhiêu tiền crawl" mà `levelup_be` cần để quy chi phí (§6, §9.4)."""
    rows = (
        await db.execute(
            select(CrawlAttempt.source, CrawlAttempt.cost_usd, CrawlAttempt.request_id).where(
                CrawlAttempt.run_id == run_id
            )
        )
    ).all()
    total_cost = sum(float(r.cost_usd) for r in rows)
    by_source: dict[str, float] = {}
    for r in rows:
        by_source[r.source] = by_source.get(r.source, 0.0) + float(r.cost_usd)
    return {
        "run_id": run_id,
        "total_cost_usd": round(total_cost, 6),
        "distinct_requests": len({r.request_id for r in rows}),
        "attempts": len(rows),
        "cost_by_source": {k: round(v, 6) for k, v in by_source.items()},
    }


async def success_rate(db: AsyncSession, source: str, *, limit: int = 500) -> dict:
    """Tỉ lệ `OK`/`EMPTY` (thành công) trên tổng số attempt gần nhất — chỉ số vận
    hành chính theo §6. Dùng cho script demo và sau này cho một endpoint dashboard."""
    rows = (
        (
            await db.execute(
                select(CrawlAttempt.outcome)
                .where(CrawlAttempt.source == source)
                .order_by(CrawlAttempt.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    total = len(rows)
    ok = sum(1 for o in rows if o in (Outcome.OK.value, Outcome.EMPTY.value))
    by_outcome: dict[str, int] = {}
    for o in rows:
        by_outcome[o] = by_outcome.get(o, 0) + 1
    return {
        "source": source,
        "total_attempts": total,
        "success_rate": round(ok / total, 4) if total else None,
        "by_outcome": by_outcome,
    }
