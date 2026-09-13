"""Ghi/đọc thực thể TMĐT.

Hai điểm đáng chú ý so với `crud/results.py` hiện có:

  - **Upsert theo khoá tự nhiên** thay vì insert mỗi lần quét. Quét lại cùng store
    không nhân đôi dữ liệu, chỉ cập nhật `last_seen_at` + giá.

  - **Điểm giá chỉ ghi khi giá ĐỔI.** Ghi mỗi lần quét làm bảng phình tuyến tính mà
    không thêm thông tin — việc "hôm nay vẫn còn bán" đã nằm ở `last_seen_at`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.ecom import EcomPricePoint, EcomProduct

_PRODUCT_UPDATABLE = (
    "title",
    "url",
    "brand",
    "product_type",
    "currency",
    "price_min_minor",
    "price_max_minor",
    "rating",
    "review_count",
    "sales_volume",
    "bestseller_rank",
    "available",
    "seller",
    "is_sponsored",
    "image_refs",
    "raw",
)


async def upsert_products(db: AsyncSession, products: Iterable[dict]) -> dict[str, int]:
    """Chèn mới hoặc cập nhật theo (source, shop_domain, external_id).

    Trả về số lượng để biết lần quét này thật sự mang lại bao nhiêu thứ mới —
    chỉ số hữu ích khi đánh giá một nguồn có đáng tiền hay không.
    """
    # Gộp trùng theo khoá tự nhiên TRƯỚC khi gửi xuống DB.
    #
    # Postgres từ chối `ON CONFLICT DO UPDATE` nếu trong cùng một câu lệnh có hai dòng
    # cùng khoá ("cannot affect row a second time"). Chuyện này xảy ra thật: quét nhiều
    # trang thì sàn hay trả lại cùng một sản phẩm ở trang sau (do sắp xếp đổi, quảng cáo
    # chèn giữa...). Giữ bản ghi xuất hiện SAU vì nó mới hơn.
    deduped: dict[tuple[str, str, str], dict] = {}
    for p in products:
        row = {k: v for k, v in p.items() if k != "variants"}
        deduped[(row["source"], row["shop_domain"], row["external_id"])] = row

    rows = list(deduped.values())
    if not rows:
        return {"total": 0, "inserted": 0, "updated": 0}

    stmt = pg_insert(EcomProduct).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ecom_product_natural",
        set_={
            **{c: stmt.excluded[c] for c in _PRODUCT_UPDATABLE},
            "last_seen_at": func.now(),
        },
    # `xmax = 0` là mẹo chuẩn của Postgres để phân biệt dòng vừa INSERT với dòng bị UPDATE.
    # Phải dùng literal_column().label() chứ không phải text("... AS inserted"):
    # với raw text, SQLAlchemy không gán được key nên `row.inserted` ném AttributeError.
    ).returning(EcomProduct.id, literal_column("(xmax = 0)").label("inserted"))

    result = await db.execute(stmt)
    outcome = result.all()
    await db.commit()

    inserted = sum(1 for r in outcome if r.inserted)
    return {"total": len(outcome), "inserted": inserted, "updated": len(outcome) - inserted}


async def save_price_points(
    db: AsyncSession,
    *,
    request_id: str,
    source: str,
    shop_domain: str,
    products: Iterable[dict],
) -> int:
    """Ghi điểm giá — chỉ những variant có giá KHÁC lần quan sát gần nhất."""
    # Cùng lý do dedupe ở upsert_products: một variant có thể gặp lại ở trang sau.
    # Ở đây không phải vì ràng buộc DB mà để không ghi hai điểm giá giống hệt nhau.
    by_variant: dict[str, dict] = {}
    for p in products:
        for v in p.get("variants") or []:
            if v.get("price_minor") is None:
                continue
            by_variant[v["external_variant_id"]] = (
                {
                    "request_id": request_id,
                    "source": source,
                    "shop_domain": shop_domain,
                    "external_product_id": p["external_id"],
                    "external_variant_id": v["external_variant_id"],
                    "variant_title": v.get("variant_title"),
                    "sku": v.get("sku"),
                    "currency": p.get("currency"),
                    "price_minor": v["price_minor"],
                    "compare_at_minor": v.get("compare_at_minor"),
                    "available": v.get("available"),
                }
            )

    candidates = list(by_variant.values())
    if not candidates:
        return 0

    latest = await _latest_prices(
        db, source, shop_domain, [c["external_variant_id"] for c in candidates]
    )
    changed = [c for c in candidates if latest.get(c["external_variant_id"]) != c["price_minor"]]
    if not changed:
        return 0

    db.add_all(EcomPricePoint(**c) for c in changed)
    await db.commit()
    return len(changed)


async def _latest_prices(
    db: AsyncSession, source: str, shop_domain: str, variant_ids: list[str]
) -> dict[str, int]:
    """Giá gần nhất của từng variant — một query `DISTINCT ON`, không phải N query."""
    if not variant_ids:
        return {}

    stmt = (
        select(EcomPricePoint.external_variant_id, EcomPricePoint.price_minor)
        .where(
            EcomPricePoint.source == source,
            EcomPricePoint.shop_domain == shop_domain,
            EcomPricePoint.external_variant_id.in_(variant_ids),
        )
        .distinct(EcomPricePoint.external_variant_id)
        .order_by(EcomPricePoint.external_variant_id, EcomPricePoint.observed_at.desc())
    )
    rows = await db.execute(stmt)
    return {vid: price for vid, price in rows.all()}


# ── Đọc — phục vụ AI Service ─────────────────────────────────────────────────


async def list_products(
    db: AsyncSession,
    *,
    source: str | None = None,
    shop_domain: str | None = None,
    min_rating: float | None = None,
    updated_since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[EcomProduct]:
    """`updated_since` cho phép AI đọc tăng dần thay vì quét lại toàn bảng mỗi lần
    chạy matching."""
    q = select(EcomProduct).order_by(EcomProduct.last_seen_at.desc())
    if source:
        q = q.where(EcomProduct.source == source)
    if shop_domain:
        q = q.where(EcomProduct.shop_domain == shop_domain)
    if min_rating is not None:
        q = q.where(EcomProduct.rating >= min_rating)
    if updated_since is not None:
        q = q.where(EcomProduct.last_seen_at >= updated_since)
    result = await db.execute(q.limit(limit).offset(offset))
    return list(result.scalars().all())


async def list_price_points(
    db: AsyncSession,
    *,
    source: str | None = None,
    external_product_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[EcomPricePoint]:
    q = select(EcomPricePoint).order_by(EcomPricePoint.observed_at.desc())
    if source:
        q = q.where(EcomPricePoint.source == source)
    if external_product_id:
        q = q.where(EcomPricePoint.external_product_id == external_product_id)
    result = await db.execute(q.limit(limit).offset(offset))
    return list(result.scalars().all())


def to_dict(p: EcomProduct) -> dict[str, Any]:
    return {
        "id": p.id,
        "source": p.source,
        "shop_domain": p.shop_domain,
        "external_id": p.external_id,
        "title": p.title,
        "url": p.url,
        "brand": p.brand,
        "product_type": p.product_type,
        "currency": p.currency,
        "price_min_minor": p.price_min_minor,
        "price_max_minor": p.price_max_minor,
        "rating": float(p.rating) if p.rating is not None else None,
        "review_count": p.review_count,
        "sales_volume": p.sales_volume,
        "available": p.available,
        "seller": p.seller,
        "is_sponsored": p.is_sponsored,
        "image_refs": p.image_refs,
        "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else None,
        "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
    }
