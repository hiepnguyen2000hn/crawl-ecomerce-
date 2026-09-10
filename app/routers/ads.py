import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import results as results_crud
from app.database import get_db
from app.deps import get_arq, get_job_store
from app.jobs.store import JobStore
from app.schemas.facebook_ads import FacebookAdsListItem, FacebookAdsRequest

router = APIRouter(prefix="/api/v1/ads", tags=["Facebook Ads"])


@router.post("/search", status_code=202, summary="Enqueue a Facebook Ads Library scrape via Apify")
async def search_ads(
    body: FacebookAdsRequest,
    store: Annotated[JobStore, Depends(get_job_store)],
    arq=Depends(get_arq),
) -> dict:
    """Apify actor run có thể mất tới vài phút (xem worker.py) — endpoint này chỉ enqueue
    rồi trả 202 ngay, không chờ crawl xong. Poll GET /api/v1/ads/jobs/{job_id} để lấy kết quả."""
    job_id = str(uuid.uuid4())
    apify_input = body.to_apify_input()

    await store.create(job_id, "facebook_ads_search")
    await arq.enqueue_job("run_facebook_ads_search", job_id, apify_input, body.model_dump())
    return {"job_id": job_id, "status": "QUEUED"}


@router.get("/jobs/{job_id}", summary="Poll status/result of a Facebook Ads scrape job")
async def get_ads_job(
    job_id: str,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> dict:
    data = await store.get(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return data


@router.get(
    "",
    response_model=list[FacebookAdsListItem],
    summary="List stored Facebook Ads results",
)
async def list_ads(
    db: Annotated[AsyncSession, Depends(get_db)],
    query: str | None = Query(default=None),
    page_id: str | None = Query(default=None),
    country: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[FacebookAdsListItem]:
    rows = await results_crud.list_facebook_ads_results(
        db, query=query, page_id=page_id, country=country, limit=limit, offset=offset
    )
    return [
        FacebookAdsListItem(
            id=r.id,
            request_id=r.request_id,
            query=r.query,
            page_id=r.page_id,
            country=r.country,
            total_ads_count=r.total_ads_count,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
