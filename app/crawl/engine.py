"""Pipeline chịu lỗi dùng chung cho mọi nguồn — trái tim của G1.

Đi từ tier ưu tiên cao nhất xuống thấp (`registry.chain`), dừng ở tier đầu tiên trả
`OK`. Mỗi lần thử đều được `classify` rồi ghi vào `crawl_attempts` — kể cả khi
adapter ném exception — để không lọt trường hợp nào ra khỏi việc đo đạc.

Phạm vi G1 (xem docs/DEV-Design-Crawl-Engine.md §7.3, §11):
  - CÓ: policy từ DB, cache theo idempotency key, trần chi phí/ngày, phân loại,
    ghi `crawl_attempts`, fallback theo tier, retry cùng tier.
  - CHƯA CÓ (để G2+): identity pool thật (proxy/fingerprint/cookie xoay vòng) —
    adapter nào khai `needs_identity=True` sẽ bị bỏ qua có log cảnh báo, không phải
    lỗi; pacing/concurrency vẫn trong-process, chưa qua Redis; T3 "trả bản cũ kèm
    nhãn stale" chưa có kho riêng, tạm thời chỉ trả lại kết quả thất bại cuối cùng.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl import budget, pacing
from app.crawl.cache import cache_lookup, cache_store
from app.crawl.classify import classify
from app.crawl.contracts import FetchRequest, FetchResult
from app.crawl.outcomes import RETRY_SAME_IDENTITY, Outcome
from app.crawl.policy import policy_for
from app.crud.crawl_ops import record_attempt
from app.sources import registry

logger = logging.getLogger(__name__)


async def fetch(req: FetchRequest, db: AsyncSession, redis: Any = None) -> FetchResult:
    policy = await policy_for(db, req.source)

    if not policy.enabled:
        return FetchResult(
            outcome=Outcome.NOT_AVAILABLE, error=f"Nguồn '{req.source}' đang tắt trong policy"
        )

    if policy.cache_ttl_seconds > 0:
        cached = await cache_lookup(redis, req.idempotency_key)
        if cached is not None:
            return cached

    try:
        await budget.guard_daily_cap(db, req.source, policy.daily_budget_usd)
    except budget.BudgetExceeded as exc:
        logger.error("Chặn trước khi crawl '%s': %s", req.source, exc)
        return FetchResult(outcome=Outcome.QUOTA_EXHAUSTED, error=str(exc))

    request_id = str(uuid.uuid4())
    chain = registry.chain(req.source, req.capability, policy.tier_chain)
    if not chain:
        return FetchResult(
            outcome=Outcome.NOT_AVAILABLE,
            error=f"Không có adapter nào cho nguồn='{req.source}' capability='{req.capability}'",
        )

    last: FetchResult | None = None

    for adapter in chain:
        if adapter.needs_identity:
            logger.warning(
                "Adapter %s/%s cần identity nhưng identity pool (G2) chưa tồn tại — bỏ qua tier",
                req.source,
                adapter.tier,
            )
            continue

        outcome: Outcome = Outcome.UPSTREAM_ERROR
        for attempt_no in range(1, policy.max_attempts + 1):
            async with pacing.acquire_slot(
                req.source, policy.max_concurrency, policy.min_delay_ms, policy.jitter_ms
            ):
                try:
                    result = await adapter.fetch(req, None)
                except Exception as exc:  # noqa: BLE001 — biên adapter, phải bắt hết để phân loại
                    result = FetchResult(
                        outcome=Outcome.UPSTREAM_ERROR,
                        tier_used=adapter.tier,
                        error=f"{type(exc).__name__}: {exc}",
                    )

                outcome = classify(result, req.source)
                result.outcome = outcome
                await record_attempt(
                    db,
                    request_id=request_id,
                    req=req,
                    tier=adapter.tier,
                    attempt_no=attempt_no,
                    outcome=outcome,
                    result=result,
                )

            last = result
            if outcome is Outcome.OK or outcome not in RETRY_SAME_IDENTITY:
                break  # OK → xong; còn lại (trừ RATE_LIMITED/UPSTREAM_ERROR) thử lại vô ích

        if last is not None and last.outcome is Outcome.OK:
            if policy.cache_ttl_seconds > 0:
                await cache_store(redis, req.idempotency_key, last, policy.cache_ttl_seconds)
            return last

        if last is not None and last.outcome is Outcome.EMPTY:
            # "Không retry — đây là câu trả lời hợp lệ" áp dụng cho toàn bộ chuỗi
            # tier, không chỉ tier hiện tại: 0 kết quả thật thì tier khác cũng vậy.
            return last

        # BLOCKED / NOT_AVAILABLE / PARSE_FAIL / QUOTA_EXHAUSTED / hết max_attempts
        # với RATE_LIMITED-UPSTREAM_ERROR → nhảy sang tier kế tiếp trong chuỗi.

    # T3 thật (trả bản cache cũ kèm nhãn stale) chưa có kho riêng ở G1 — trả lại
    # kết quả thất bại cuối cùng để tầng gọi tự quyết định.
    return last or FetchResult(
        outcome=Outcome.NOT_AVAILABLE, error="Mọi tier đều cần identity chưa sẵn sàng"
    )
