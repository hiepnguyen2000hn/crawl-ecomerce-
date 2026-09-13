"""arq WorkerSettings + job functions.

Facebook Ads search chạy nền vì Apify actor có thể mất tới vài phút (poll status
mỗi 3s, timeout 300s — xem app/services/apify_client.py). Chạy việc này trong
HTTP request sẽ vượt timeout của reverse proxy và giữ connection pool vô ích;
router chỉ enqueue rồi trả 202 ngay, kết quả lấy qua GET /api/v1/ads/jobs/{job_id}.
"""

import redis.asyncio as aioredis
from arq import cron, create_pool
from arq.connections import RedisSettings as ArqRedisSettings

from app.config import settings
from app.crud import audit_log as audit_crud
from app.crud import results as results_crud
from app.database import AsyncSessionLocal
from app.jobs.crawl_jobs import run_bol_search, run_reddit_voc, run_shopify_scan
from app.jobs.store import JobStatus, JobStore
from app.jobs.watchlist_jobs import run_watchlist_tick
from app.services.apify_client import ApifyError, apify_client


async def run_facebook_ads_search(ctx: dict, job_id: str, apify_input: dict, request_meta: dict) -> None:
    """job_id được dùng luôn làm request_id trong audit_logs/facebook_ads_results — 1 id duy nhất
    xuyên suốt job, khỏi phải theo dõi 2 định danh khác nhau cho cùng 1 lần crawl."""
    store: JobStore = ctx["job_store"]
    await store.set_status(job_id, JobStatus.RUNNING)

    async with AsyncSessionLocal() as db:
        try:
            ads, latency_ms = await apify_client.run_actor(apify_input)

            await audit_crud.create_log(
                db,
                request_id=job_id,
                endpoint="/api/v1/ads/search",
                request_params=apify_input,
                response_data={"total": len(ads), "items": ads[:5]},
                status="success",
                http_status_code=200,
                latency_ms=latency_ms,
            )
            await results_crud.save_facebook_ads_result(
                db,
                request_id=job_id,
                query=request_meta.get("query"),
                page_id=request_meta.get("page_id"),
                country=request_meta.get("country", ""),
                category=request_meta.get("category", "all"),
                media_type=request_meta.get("media_type", "all"),
                active_status=request_meta.get("active_status", "active"),
                min_date=request_meta.get("min_date"),
                max_date=request_meta.get("max_date"),
                fetch_details=request_meta.get("fetch_details", False),
                ads_data=ads,
            )

            await store.set_status(
                job_id,
                JobStatus.SUCCEEDED,
                result={"request_id": job_id, "total_ads": len(ads), "latency_ms": latency_ms},
            )
        except ApifyError as exc:
            await audit_crud.create_log(
                db,
                request_id=job_id,
                endpoint="/api/v1/ads/search",
                request_params=apify_input,
                response_data=None,
                status="error",
                http_status_code=exc.status_code or 502,
                latency_ms=None,
                error_message=str(exc),
            )
            await store.set_status(job_id, JobStatus.FAILED, error=str(exc))


async def on_startup(ctx: dict) -> None:
    ctx["redis_client"] = aioredis.from_url(settings.redis_url)
    ctx["job_store"] = JobStore(ctx["redis_client"], settings.job_ttl_seconds)
    # Cron cần tự enqueue job crawl → phải có pool riêng, `ctx["redis"]` của arq
    # là connection của worker chứ không phải producer.
    ctx["arq_pool"] = await create_pool(ArqRedisSettings.from_dsn(settings.redis_url))


async def on_shutdown(ctx: dict) -> None:
    client = ctx.get("redis_client")
    if client is not None:
        await client.close()
    pool = ctx.get("arq_pool")
    if pool is not None:
        await pool.close()


class WorkerSettings:
    functions = [
        run_facebook_ads_search,
        run_shopify_scan,
        run_bol_search,
        run_reddit_voc,
        run_watchlist_tick,
    ]
    # Tick mỗi giờ; từng mục trong watchlist tự quyết đã tới hạn chưa theo
    # `interval_hours` của nó. Xem app/jobs/watchlist_jobs.py.
    cron_jobs = [cron(run_watchlist_tick, minute={5}, run_at_startup=False)]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = ArqRedisSettings.from_dsn(settings.redis_url)
