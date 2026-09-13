"""Ghi/đọc chỉ số theo thời gian và danh sách theo dõi."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking import CrawlWatchlist, ProductMetricSnapshot

#: Chỉ số nào được coi là "đã đổi" thì mới ghi dòng mới.
_TRACKED = ("review_count", "rating", "available", "bestseller_rank")


async def save_snapshots(
    db: AsyncSession, *, source: str, shop_domain: str, products: Iterable[dict]
) -> int:
    """Ghi chỉ số — chỉ những sản phẩm có ít nhất một chỉ số khác lần quan sát trước."""
    candidates: dict[str, dict] = {}
    for p in products:
        if all(p.get(k) is None for k in _TRACKED):
            continue  # không có chỉ số nào để theo dõi thì khỏi ghi
        candidates[p["external_id"]] = {
            "source": source,
            "shop_domain": shop_domain,
            "external_product_id": p["external_id"],
            "review_count": p.get("review_count"),
            "rating": p.get("rating"),
            "available": p.get("available"),
            "bestseller_rank": p.get("bestseller_rank"),
        }

    if not candidates:
        return 0

    previous = await _latest_snapshots(db, source, shop_domain, list(candidates))
    changed = [
        row
        for pid, row in candidates.items()
        if _differs(row, previous.get(pid))
    ]
    if not changed:
        return 0

    db.add_all(ProductMetricSnapshot(**row) for row in changed)
    await db.commit()
    return len(changed)


def _differs(now: dict, before: dict | None) -> bool:
    if before is None:
        return True
    for k in _TRACKED:
        a, b = now.get(k), before.get(k)
        # rating lưu Numeric → so sánh dạng float để 4.60 và 4.6 không bị coi là khác nhau
        if k == "rating":
            a = float(a) if a is not None else None
            b = float(b) if b is not None else None
        if a != b:
            return True
    return False


async def _latest_snapshots(
    db: AsyncSession, source: str, shop_domain: str, product_ids: list[str]
) -> dict[str, dict]:
    """Quan sát gần nhất của từng sản phẩm — một query `DISTINCT ON`, không phải N query."""
    if not product_ids:
        return {}
    stmt = (
        select(
            ProductMetricSnapshot.external_product_id,
            ProductMetricSnapshot.review_count,
            ProductMetricSnapshot.rating,
            ProductMetricSnapshot.available,
            ProductMetricSnapshot.bestseller_rank,
        )
        .where(
            ProductMetricSnapshot.source == source,
            ProductMetricSnapshot.shop_domain == shop_domain,
            ProductMetricSnapshot.external_product_id.in_(product_ids),
        )
        .distinct(ProductMetricSnapshot.external_product_id)
        .order_by(
            ProductMetricSnapshot.external_product_id,
            ProductMetricSnapshot.observed_at.desc(),
        )
    )
    rows = await db.execute(stmt)
    return {
        r.external_product_id: {
            "review_count": r.review_count,
            "rating": r.rating,
            "available": r.available,
            "bestseller_rank": r.bestseller_rank,
        }
        for r in rows.all()
    }


# ── Tốc độ tăng review — proxy cho doanh số ──────────────────────────────────

_VELOCITY_SQL = text(
    """
WITH latest AS (
    SELECT DISTINCT ON (source, shop_domain, external_product_id)
           source, shop_domain, external_product_id, review_count, observed_at
    FROM product_metric_snapshots
    WHERE review_count IS NOT NULL
    ORDER BY source, shop_domain, external_product_id, observed_at DESC
),
-- Mốc so sánh: quan sát gần nhất TRƯỚC cửa sổ. Nếu sản phẩm mới theo dõi chưa đủ
-- lâu thì không có dòng nào ở đây, và ta rơi xuống `earliest` bên dưới.
asof AS (
    SELECT DISTINCT ON (source, shop_domain, external_product_id)
           source, shop_domain, external_product_id, review_count, observed_at
    FROM product_metric_snapshots
    WHERE review_count IS NOT NULL AND observed_at <= :cutoff
    ORDER BY source, shop_domain, external_product_id, observed_at DESC
),
earliest AS (
    SELECT DISTINCT ON (source, shop_domain, external_product_id)
           source, shop_domain, external_product_id, review_count, observed_at
    FROM product_metric_snapshots
    WHERE review_count IS NOT NULL
    ORDER BY source, shop_domain, external_product_id, observed_at ASC
)
SELECT p.source, p.shop_domain, p.external_id, p.title, p.url,
       p.currency, p.price_min_minor, p.rating, p.review_count AS review_hien_tai,
       p.seller, p.is_sponsored,
       COALESCE(a.review_count, e.review_count)             AS review_moc,
       COALESCE(a.observed_at,  e.observed_at)              AS moc_luc,
       l.observed_at                                        AS quan_sat_luc,
       EXTRACT(EPOCH FROM (l.observed_at - COALESCE(a.observed_at, e.observed_at)))
           / 86400.0                                        AS so_ngay
FROM ecom_products p
JOIN latest   l ON (l.source, l.shop_domain, l.external_product_id)
                 = (p.source, p.shop_domain, p.external_id)
LEFT JOIN asof     a ON (a.source, a.shop_domain, a.external_product_id)
                      = (p.source, p.shop_domain, p.external_id)
LEFT JOIN earliest e ON (e.source, e.shop_domain, e.external_product_id)
                      = (p.source, p.shop_domain, p.external_id)
-- Phải CAST rõ ràng: asyncpg suy kiểu tham số từ ngữ cảnh, mà `$n IS NULL` không
-- cho nó manh mối nào → "could not determine data type of parameter".
WHERE (CAST(:source AS text) IS NULL OR p.source = CAST(:source AS text))
"""
)


async def review_velocity(
    db: AsyncSession,
    *,
    window_days: int = 30,
    source: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Tốc độ tăng review — dùng thay cho `Sales Volume` mà 2/3 nguồn không công bố.

    Luôn trả kèm `so_ngay_theo_doi`: một con số tính trên 2 ngày dữ liệu thì rất khác
    một con số tính trên 30 ngày, và người đọc phải thấy được sự khác nhau đó.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = await db.execute(_VELOCITY_SQL, {"cutoff": cutoff, "source": source})

    out: list[dict[str, Any]] = []
    for r in rows.mappings().all():
        days = float(r["so_ngay"] or 0)
        delta = (r["review_hien_tai"] or 0) - (r["review_moc"] or 0)
        # Dưới nửa ngày thì phép chia khuếch đại nhiễu thành con số vô nghĩa.
        velocity = round(delta / days, 2) if days >= 0.5 else None

        out.append(
            {
                "source": r["source"],
                "shop_domain": r["shop_domain"],
                "external_id": r["external_id"],
                "title": r["title"],
                "url": r["url"],
                "currency": r["currency"],
                "price_min_minor": r["price_min_minor"],
                "rating": float(r["rating"]) if r["rating"] is not None else None,
                "review_hien_tai": r["review_hien_tai"],
                "review_tang_them": delta,
                "so_ngay_theo_doi": round(days, 2),
                "review_moi_ngay": velocity,
                "seller": r["seller"],
                "is_sponsored": r["is_sponsored"],
                "du_lieu_du_tin_cay": days >= 7,
            }
        )

    out.sort(key=lambda x: (x["review_moi_ngay"] or -1), reverse=True)
    return out[:limit]


# ── Danh sách theo dõi ───────────────────────────────────────────────────────


async def add_watch(
    db: AsyncSession,
    *,
    kind: str,
    target: str,
    country_path: str = "",
    max_pages: int = 2,
    interval_hours: int = 24,
    note: str | None = None,
) -> CrawlWatchlist:
    existing = await db.execute(
        select(CrawlWatchlist).where(
            CrawlWatchlist.kind == kind,
            CrawlWatchlist.target == target,
            CrawlWatchlist.country_path == country_path,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.max_pages, row.interval_hours, row.enabled = max_pages, interval_hours, True
        if note:
            row.note = note
    else:
        row = CrawlWatchlist(
            kind=kind,
            target=target,
            country_path=country_path,
            max_pages=max_pages,
            interval_hours=interval_hours,
            note=note,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_watches(db: AsyncSession) -> list[CrawlWatchlist]:
    result = await db.execute(select(CrawlWatchlist).order_by(CrawlWatchlist.id))
    return list(result.scalars().all())


async def due_watches(db: AsyncSession, now: datetime | None = None) -> list[CrawlWatchlist]:
    """Mục đã tới hạn quét lại. Mục chưa chạy lần nào (`last_run_at` NULL) luôn tới hạn."""
    now = now or datetime.now(timezone.utc)
    result = await db.execute(select(CrawlWatchlist).where(CrawlWatchlist.enabled.is_(True)))
    return [
        w
        for w in result.scalars().all()
        if w.last_run_at is None
        or w.last_run_at <= now - timedelta(hours=w.interval_hours)
    ]


async def mark_run(db: AsyncSession, watch_id: int, job_id: str) -> None:
    await db.execute(
        update(CrawlWatchlist)
        .where(CrawlWatchlist.id == watch_id)
        .values(
            last_run_at=datetime.now(timezone.utc),
            last_job_id=job_id,
            run_count=CrawlWatchlist.run_count + 1,
        )
    )
    await db.commit()


async def set_enabled(db: AsyncSession, watch_id: int, enabled: bool) -> None:
    await db.execute(
        update(CrawlWatchlist).where(CrawlWatchlist.id == watch_id).values(enabled=enabled)
    )
    await db.commit()


def watch_to_dict(w: CrawlWatchlist) -> dict[str, Any]:
    return {
        "id": w.id,
        "kind": w.kind,
        "target": w.target,
        "country_path": w.country_path or None,
        "max_pages": w.max_pages,
        "interval_hours": w.interval_hours,
        "enabled": w.enabled,
        "note": w.note,
        "run_count": w.run_count,
        "last_run_at": w.last_run_at.isoformat() if w.last_run_at else None,
        "last_job_id": w.last_job_id,
    }
