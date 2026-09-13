"""Cron quét lại danh sách theo dõi.

Chạy mỗi giờ, nhưng **không** quét mỗi giờ: mỗi mục có `interval_hours` riêng và chỉ
được enqueue khi tới hạn. Tick hàng giờ chỉ để bắt kịp mọi chu kỳ (24h, 12h, 6h...)
mà không cần nhiều lịch khác nhau.

Vì sao phải có cron chứ không để người bấm tay: tốc độ tăng review chỉ có ý nghĩa khi
chuỗi quan sát **đều đặn và không đứt quãng**. Một tuần quên quét là một tuần không
tính được velocity cho khoảng đó, và không back-fill được.
"""

from __future__ import annotations

import logging
import uuid

from app.crud import tracking as tracking_crud
from app.database import AsyncSessionLocal
from app.jobs.store import JobStore

logger = logging.getLogger(__name__)


async def run_watchlist_tick(ctx: dict) -> dict:
    """Tìm mục tới hạn và enqueue job crawl tương ứng."""
    store: JobStore = ctx["job_store"]
    arq = ctx.get("arq_pool") or ctx.get("redis")

    enqueued: list[dict] = []

    async with AsyncSessionLocal() as db:
        due = await tracking_crud.due_watches(db)

        for w in due:
            job_id = str(uuid.uuid4())

            if w.kind == "shopify_store":
                await store.create(job_id, "shopify_scan")
                await arq.enqueue_job(
                    "run_shopify_scan", job_id, w.target, w.max_pages, None
                )
            elif w.kind == "bol_query":
                await store.create(job_id, "bol_search")
                await arq.enqueue_job(
                    "run_bol_search", job_id, w.target, w.max_pages, w.country_path or "/nl/nl"
                )
            else:
                logger.warning("Watchlist kind không nhận diện được: %s (id=%s)", w.kind, w.id)
                continue

            await tracking_crud.mark_run(db, w.id, job_id)
            enqueued.append({"watch_id": w.id, "kind": w.kind, "target": w.target, "job_id": job_id})

    if enqueued:
        logger.info("Watchlist tick: enqueue %d job", len(enqueued))
    return {"enqueued": len(enqueued), "items": enqueued}
