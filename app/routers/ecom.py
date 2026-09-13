"""Endpoint TMĐT — Shopify (F2.4 giá đối thủ) và Bol.com (F2.3 sản phẩm sàn).

Mọi việc quét đều là job bất đồng bộ: crawl nhiều trang mất vài chục giây tới vài
phút, chạy trong HTTP request sẽ vượt timeout của reverse proxy. Router chỉ enqueue
rồi trả 202 — cùng pattern với `routers/ads.py`.

Riêng `/probe` là đồng bộ và cố ý như vậy: nó chỉ gọi 1 request để kiểm tra nhanh
xem parser đọc được gì, dùng trước khi chạy job hàng loạt.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import ecom as ecom_crud
from app.crud import ads as ads_crud
from app.crud import tracking as tracking_crud
from app.crud import voc as voc_crud
from app.database import get_db
from app.deps import get_arq, get_job_store
from app.jobs.store import JobStore
from app.schemas.ecom import (
    BolSearchRequest,
    JobAccepted,
    ProductListItem,
    ShopifyScanRequest,
)
from app.services import bol_client, excel_export, shopify_client

router = APIRouter(prefix="/api/v1/ecom", tags=["E-commerce"])


# ── Shopify ──────────────────────────────────────────────────────────────────


@router.post(
    "/shopify/scan",
    status_code=202,
    response_model=JobAccepted,
    summary="Quét catalog công khai của một store Shopify",
)
async def scan_shopify(
    body: ShopifyScanRequest,
    store: Annotated[JobStore, Depends(get_job_store)],
    arq=Depends(get_arq),
) -> JobAccepted:
    job_id = str(uuid.uuid4())
    await store.create(job_id, "shopify_scan")
    await arq.enqueue_job(
        "run_shopify_scan", job_id, body.shop_domain, body.max_pages, body.currency
    )
    return JobAccepted(job_id=job_id)


@router.get(
    "/shopify/probe",
    summary="Kiểm tra nhanh một domain có phải store Shopify không (đồng bộ, 1 request)",
)
async def probe_shopify(
    shop_domain: str = Query(..., description="Domain hoặc URL đầy đủ"),
) -> dict:
    """Dùng trước khi chạy job: biết ngay store có bật /products.json, có đặt mật khẩu,
    hay có mã tiền tệ gì — thay vì enqueue rồi đợi mới phát hiện."""
    result = await shopify_client.scan_store(shop_domain, max_pages=1)
    return {
        "shop_domain": result.shop_domain,
        "outcome": result.outcome,
        "currency": result.currency,
        "products_found": len(result.products),
        "sample": [
            {
                "title": p["title"],
                "price_min_minor": p["price_min_minor"],
                "variants": len(p["variants"]),
            }
            for p in result.products[:5]
        ],
        "error": result.error,
    }


# ── Bol.com ──────────────────────────────────────────────────────────────────


@router.post(
    "/bol/search",
    status_code=202,
    response_model=JobAccepted,
    summary="Tìm sản phẩm trên Bol.com theo từ khoá",
)
async def search_bol(
    body: BolSearchRequest,
    store: Annotated[JobStore, Depends(get_job_store)],
    arq=Depends(get_arq),
) -> JobAccepted:
    job_id = str(uuid.uuid4())
    await store.create(job_id, "bol_search")
    await arq.enqueue_job(
        "run_bol_search", job_id, body.query, body.max_pages, body.country_path
    )
    return JobAccepted(job_id=job_id)


@router.get(
    "/bol/probe",
    summary="Kiểm tra parser Bol.com đang đọc được gì (đồng bộ, 1 trang)",
)
async def probe_bol(
    query: str = Query(..., min_length=1),
    country_path: str = Query(default="/nl/nl"),
) -> dict:
    """Bol.com không có API nên parser phụ thuộc cấu trúc trang. Endpoint này cho biết
    parser đang chạy nhánh nào (`json-ld` bền / `css` dễ vỡ) và đọc ra được gì —
    kiểm chứng trước khi tin vào dữ liệu hàng loạt."""
    result = await bol_client.search(query, max_pages=1, country_path=country_path)
    return {
        "query": query,
        "outcome": result.outcome,
        "parse_source": result.parse_source,
        "products_found": len(result.products),
        "sample": [
            {
                "title": p["title"],
                "price_min_minor": p["price_min_minor"],
                "currency": p["currency"],
                "rating": p["rating"],
                "review_count": p["review_count"],
                "url": p["url"],
            }
            for p in result.products[:5]
        ],
        "error": result.error,
    }


# ── Job status ───────────────────────────────────────────────────────────────


@router.get("/jobs/{job_id}", summary="Xem trạng thái/kết quả một job quét")
async def get_job(job_id: str, store: Annotated[JobStore, Depends(get_job_store)]) -> dict:
    data = await store.get(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy job '{job_id}'")
    return data


# ── Đọc dữ liệu — phục vụ AI Service ────────────────────────────────────────


@router.get(
    "/products",
    response_model=list[ProductListItem],
    summary="Danh sách sản phẩm đã chuẩn hoá",
)
async def list_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    source: str | None = Query(default=None, description="'shopify' | 'bol'"),
    shop_domain: str | None = Query(default=None),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    updated_since: datetime | None = Query(
        default=None, description="Đọc tăng dần — chỉ lấy thứ đổi từ mốc này"
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProductListItem]:
    rows = await ecom_crud.list_products(
        db,
        source=source,
        shop_domain=shop_domain,
        min_rating=min_rating,
        updated_since=updated_since,
        limit=limit,
        offset=offset,
    )
    return [ProductListItem(**ecom_crud.to_dict(r)) for r in rows]


@router.get(
    "/export.xlsx",
    summary="Xuất dữ liệu đã crawl ra file Excel",
    response_class=StreamingResponse,
)
async def export_excel(
    db: Annotated[AsyncSession, Depends(get_db)],
    source: str | None = Query(default=None, description="'shopify' | 'bol' — bỏ trống = tất cả"),
    shop_domain: str | None = Query(default=None),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    updated_since: datetime | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=50000),
    include_voc: bool = Query(default=True, description="Kèm sheet Reddit VOC nếu có dữ liệu"),
) -> StreamingResponse:
    """3 sheet: San pham · Lich su gia · Reddit VOC.

    Giá được quy từ minor unit về đơn vị chính (8999 → 89,99) và giữ kiểu số của Excel,
    nên vẫn lọc/sắp/tính công thức được — không phải chuỗi.
    """
    products = await ecom_crud.list_products(
        db,
        source=source,
        shop_domain=shop_domain,
        min_rating=min_rating,
        updated_since=updated_since,
        limit=limit,
        offset=0,
    )
    price_points = await ecom_crud.list_price_points(db, source=source, limit=limit, offset=0)
    threads = await voc_crud.list_threads(db, limit=1000) if include_voc else []
    velocity = await tracking_crud.review_velocity(db, source=source, limit=limit)
    ads = await ads_crud.list_ads(db, limit=min(limit, 2000))
    lps = await ads_crud.landing_pages(db, min_active_days=7, limit=300)

    buf = excel_export.build_workbook(products, price_points, threads, velocity, ads, lps)
    name = excel_export.filename("levelup-crawl")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/price-points", summary="Lịch sử giá (chỉ ghi khi giá đổi)")
async def list_price_points(
    db: Annotated[AsyncSession, Depends(get_db)],
    source: str | None = Query(default=None),
    external_product_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    rows = await ecom_crud.list_price_points(
        db, source=source, external_product_id=external_product_id, limit=limit, offset=offset
    )
    return [
        {
            "id": r.id,
            "source": r.source,
            "shop_domain": r.shop_domain,
            "external_product_id": r.external_product_id,
            "external_variant_id": r.external_variant_id,
            "variant_title": r.variant_title,
            "sku": r.sku,
            "currency": r.currency,
            "price_minor": r.price_minor,
            "compare_at_minor": r.compare_at_minor,
            "available": r.available,
            "observed_at": r.observed_at.isoformat(),
        }
        for r in rows
    ]
