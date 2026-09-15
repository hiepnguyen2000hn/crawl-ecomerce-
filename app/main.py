from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings as ArqRedisSettings
from fastapi import FastAPI

from app.config import settings
from app.database import init_db
from app.jobs.store import JobStore
from app.routers.ads import router as ads_router
from app.routers.ai import router as ai_router
from app.routers.browser import router as browser_router
from app.routers.ecom import router as ecom_router
from app.routers.provider import router as provider_router
from app.routers.tracking import router as tracking_router
from app.routers.trends import audit_router, router as trends_router
from app.routers.voc import router as voc_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.arq_redis = await create_pool(ArqRedisSettings.from_dsn(settings.redis_url))
    app.state.job_store = JobStore(app.state.arq_redis, settings.job_ttl_seconds)
    try:
        yield
    finally:
        await app.state.arq_redis.close()


app = FastAPI(
    title="Crawl E-Commerce API",
    description="Data pipeline for e-commerce trend analysis via SerpAPI + Google Trends",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(trends_router)
app.include_router(audit_router)
app.include_router(provider_router)
app.include_router(ads_router)
app.include_router(ai_router)
app.include_router(ecom_router)
app.include_router(voc_router)
app.include_router(tracking_router)
app.include_router(browser_router)


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "version": "0.2.0"}
