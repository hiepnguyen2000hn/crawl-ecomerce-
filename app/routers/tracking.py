"""Endpoint theo dõi chỉ số theo thời gian và quản lý danh sách quét định kỳ."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.normalize import normalize_domain
from app.crud import tracking as tracking_crud
from app.database import get_db

router = APIRouter(prefix="/api/v1/tracking", tags=["Theo doi chi so"])


class WatchIn(BaseModel):
    kind: Literal["shopify_store", "bol_query"]
    target: str = Field(..., min_length=1, description="Domain store, hoặc từ khoá Bol.com")
    country_path: str = Field(default="", description="Chỉ cho bol_query: /nl/nl hoặc /be/nl")
    max_pages: int = Field(default=2, ge=1, le=20)
    interval_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="24h là hợp lý: review không tăng đủ nhanh để quét dày hơn có ý nghĩa",
    )
    note: str | None = None


@router.post("/watchlist", status_code=201, summary="Thêm mục vào danh sách quét định kỳ")
async def add_watch(body: WatchIn, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    target = normalize_domain(body.target) if body.kind == "shopify_store" else body.target.strip()
    country = body.country_path if body.kind == "bol_query" else ""
    row = await tracking_crud.add_watch(
        db,
        kind=body.kind,
        target=target,
        country_path=country,
        max_pages=body.max_pages,
        interval_hours=body.interval_hours,
        note=body.note,
    )
    return tracking_crud.watch_to_dict(row)


@router.get("/watchlist", summary="Danh sách đang quét định kỳ")
async def list_watches(db: Annotated[AsyncSession, Depends(get_db)]) -> list[dict]:
    return [tracking_crud.watch_to_dict(w) for w in await tracking_crud.list_watches(db)]


@router.patch("/watchlist/{watch_id}", summary="Bật/tắt một mục")
async def toggle_watch(
    watch_id: int, enabled: bool, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict:
    watches = {w.id: w for w in await tracking_crud.list_watches(db)}
    if watch_id not in watches:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy mục {watch_id}")
    await tracking_crud.set_enabled(db, watch_id, enabled)
    return {"id": watch_id, "enabled": enabled}


@router.get(
    "/velocity",
    summary="Tốc độ tăng review — chỉ số thay thế cho 'số lượng đã bán'",
)
async def velocity(
    db: Annotated[AsyncSession, Depends(get_db)],
    window_days: int = Query(default=30, ge=1, le=365),
    source: str | None = Query(default=None, description="'shopify' | 'bol'"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    """Shopify và Bol.com không công bố số đã bán, nhưng **tốc độ tăng review** là proxy
    tốt: tỉ lệ người mua để lại review khá ổn định trong cùng ngành hàng.

    Mỗi dòng kèm `so_ngay_theo_doi` và `du_lieu_du_tin_cay` — một con số tính trên 2 ngày
    rất khác một con số tính trên 30 ngày, và người đọc phải thấy được sự khác biệt đó
    thay vì tin vào một con số trần trụi.
    """
    return await tracking_crud.review_velocity(
        db, window_days=window_days, source=source, limit=limit
    )
