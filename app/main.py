from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers.trends import audit_router, router as trends_router
from app.services.serpapi_client import serpapi_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await serpapi_client.aclose()


app = FastAPI(
    title="Crawl E-Commerce API",
    description="Data pipeline for e-commerce trend analysis via SerpAPI + Google Trends",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(trends_router)
app.include_router(audit_router)


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "version": "0.1.0"}
