from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers.ads import router as ads_router
from app.routers.ai import router as ai_router
from app.routers.provider import router as provider_router
from app.routers.trends import audit_router, router as trends_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


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


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "version": "0.2.0"}
