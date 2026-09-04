import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import audit_log as audit_crud
from app.crud import results as results_crud
from app.database import get_db
from app.schemas.facebook_ads import (
    FacebookAdsListItem,
    FacebookAdsRequest,
    FacebookAdsResponse,
)
from app.services.apify_client import ApifyError, apify_client

router = APIRouter(prefix="/api/v1/ads", tags=["Facebook Ads"])


@router.post(
    "/search",
    response_model=FacebookAdsResponse,
    summary="Scrape Facebook Ads Library via Apify",
)
async def search_ads(
    body: FacebookAdsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FacebookAdsResponse:
    request_id = str(uuid.uuid4())
    endpoint = "/api/v1/ads/search"
    apify_input = body.to_apify_input()

    try:
        ads, latency_ms = await apify_client.run_actor(apify_input)

        # ── Audit log ────────────────────────────────────────────────────────
        await audit_crud.create_log(
            db,
            request_id=request_id,
            endpoint=endpoint,
            request_params=apify_input,
            response_data={"total": len(ads), "items": ads[:5]},  # preview only in audit
            status="success",
            http_status_code=200,
            latency_ms=latency_ms,
        )

        # ── Result table ──────────────────────────────────────────────────────
        await results_crud.save_facebook_ads_result(
            db,
            request_id=request_id,
            query=body.query,
            page_id=body.page_id,
            country=body.country,
            category=body.category,
            media_type=body.media_type,
            active_status=body.active_status,
            min_date=body.min_date,
            max_date=body.max_date,
            fetch_details=body.fetch_details,
            ads_data=ads,
        )

        return FacebookAdsResponse(
            request_id=request_id,
            query=body.query,
            page_id=body.page_id,
            country=body.country,
            total_ads=len(ads),
            ads=ads,
            latency_ms=latency_ms,
        )

    except ApifyError as exc:
        await audit_crud.create_log(
            db,
            request_id=request_id,
            endpoint=endpoint,
            request_params=apify_input,
            response_data=None,
            status="error",
            http_status_code=exc.status_code or 502,
            latency_ms=None,
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


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
