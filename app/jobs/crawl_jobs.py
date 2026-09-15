"""Job function cho các connector tự fetch: Shopify, Bol.com, Reddit.

Tách khỏi `worker.py` để file đó không phình khi thêm nguồn; `worker.py` chỉ còn
đăng ký danh sách function.

Quy ước chung với `run_facebook_ads_search`: `job_id` dùng luôn làm `request_id`
trong `api_audit_logs` + bảng nghiệp vụ — một id duy nhất xuyên suốt một lần crawl.

Mọi job đều ghi audit log kể cả khi thất bại, và **phân biệt outcome** thay vì chỉ
success/error: `EMPTY` không phải lỗi, `PARSE_FAIL` là lỗi cần báo động.
Xem docs/DEV-Design-Crawl-Engine.md §4.
"""

from __future__ import annotations

import logging

from app.crawl import engine
from app.crawl.contracts import Capability, FetchRequest
from app.crawl.outcomes import ALERT_WORTHY, Outcome, is_failure
from app.crud import audit_log as audit_crud
from app.crud import ecom as ecom_crud
from app.crud import tracking as tracking_crud
from app.crud import voc as voc_crud
from app.database import AsyncSessionLocal
from app.jobs.store import JobStatus, JobStore

logger = logging.getLogger(__name__)


async def _record(
    db,
    *,
    job_id: str,
    endpoint: str,
    params: dict,
    outcome: Outcome,
    latency_ms: int,
    response_data: dict | None = None,
    error: str | None = None,
) -> None:
    """Ghi audit log với outcome cụ thể, không chỉ 'success'/'error'.

    `status` giữ nguyên vocabulary cũ để tương thích với `/api/v1/audit-logs`,
    outcome chi tiết đi vào `response_data` cho tới khi có bảng `crawl_attempts` riêng.
    """
    if outcome in ALERT_WORTHY:
        logger.error(
            "PARSE_FAIL tại %s — nguồn nhiều khả năng đã đổi cấu trúc. params=%s err=%s",
            endpoint,
            params,
            error,
        )

    await audit_crud.create_log(
        db,
        request_id=job_id,
        endpoint=endpoint,
        request_params=params,
        response_data={"outcome": str(outcome), **(response_data or {})},
        status="error" if is_failure(outcome) else "success",
        http_status_code=None,
        latency_ms=latency_ms,
        error_message=error,
    )


# ── Tìm kiếm trên sàn: Amazon · 1688 · Taobao ────────────────────────────────
#
# Ba nguồn này cùng một khuôn: tìm theo từ khoá → upsert sản phẩm → ghi điểm giá +
# ảnh chụp chỉ số → ghi audit → cập nhật job store. Gói vào một chỗ thay vì chép ba
# lần: đổi cách ghi audit hay đổi payload trả ra chỉ phải sửa ở đây.
#
# `run_shopify_scan` / `run_bol_search` bên dưới giữ nguyên bản riêng — chúng có sẵn
# trước helper này và Shopify còn khác khuôn thật (quét trọn catalog, tự dò tiền tệ,
# domain đến từ kết quả chứ không từ tham số).


async def _run_marketplace_search(
    ctx: dict,
    job_id: str,
    *,
    source: str,
    endpoint: str,
    shop_domain: str,
    params: dict,
    run_id: str | None,
) -> None:
    store: JobStore = ctx["job_store"]
    await store.set_status(job_id, JobStatus.RUNNING)

    async with AsyncSessionLocal() as db:
        result = await engine.fetch(
            FetchRequest(
                source=source,
                capability=Capability.SEARCH_KEYWORD,
                params=params,
                run_id=run_id,
            ),
            db,
            ctx.get("redis_client"),
        )
        meta = result.meta
        products = result.items
        # Amazon tách dòng theo marketplace (giá amazon.de khác amazon.fr), nên domain
        # thật đến từ meta; hai sàn Alibaba thì cố định.
        domain = meta.get("marketplace") or shop_domain

        saved = {"total": 0, "inserted": 0, "updated": 0}
        price_points = snapshots = 0
        if products:
            saved = await ecom_crud.upsert_products(db, products)
            price_points = await ecom_crud.save_price_points(
                db, request_id=job_id, source=source, shop_domain=domain, products=products
            )
            snapshots = await tracking_crud.save_snapshots(
                db, source=source, shop_domain=domain, products=products
            )

        await _record(
            db,
            job_id=job_id,
            endpoint=endpoint,
            params=params,
            outcome=result.outcome,
            latency_ms=result.latency_ms,
            response_data={
                "tier": result.tier_used,
                "products": len(products),
                "pages_fetched": meta.get("pages_fetched", 0),
                "parse_source": meta.get("parse_source"),
            },
            error=result.error,
        )

        payload = {
            "request_id": job_id,
            "run_id": run_id,
            "source": source,
            "shop_domain": domain,
            "outcome": str(result.outcome),
            # Tier nào phục vụ được là thông tin vận hành quan trọng nhất ở đây: khi
            # tier vendor (trả tiền) im lặng tụt xuống browser (miễn phí nhưng dễ vỡ),
            # ta phải thấy ngay chứ không đợi tới lúc dữ liệu bắt đầu thiếu.
            "tier": result.tier_used,
            "cost_usd": round(result.cost_usd, 4),
            "parse_source": meta.get("parse_source"),
            "pages_fetched": meta.get("pages_fetched", 0),
            "products_found": len(products),
            "products_new": saved["inserted"],
            "products_updated": saved["updated"],
            "price_points_written": price_points,
            "metric_snapshots_written": snapshots,
            "latency_ms": result.latency_ms,
        }

        if is_failure(result.outcome):
            await store.set_status(job_id, JobStatus.FAILED, result=payload, error=result.error)
        else:
            await store.set_status(job_id, JobStatus.SUCCEEDED, result=payload)


async def run_amazon_search(
    ctx: dict,
    job_id: str,
    query: str,
    marketplace: str,
    max_pages: int,
    max_items: int,
    run_id: str | None = None,
) -> None:
    await _run_marketplace_search(
        ctx,
        job_id,
        source="amazon",
        endpoint="/api/v1/ecom/amazon/search",
        shop_domain=marketplace,
        params={
            "query": query,
            "marketplace": marketplace,
            "max_pages": max_pages,
            "max_items": max_items,
        },
        run_id=run_id,
    )


async def run_1688_search(
    ctx: dict,
    job_id: str,
    query: str,
    max_pages: int,
    run_id: str | None = None,
) -> None:
    await _run_marketplace_search(
        ctx,
        job_id,
        source="alibaba_1688",
        endpoint="/api/v1/ecom/1688/search",
        shop_domain="1688.com",
        params={"query": query, "max_pages": max_pages},
        run_id=run_id,
    )


async def run_taobao_search(
    ctx: dict,
    job_id: str,
    query: str,
    max_pages: int,
    run_id: str | None = None,
) -> None:
    await _run_marketplace_search(
        ctx,
        job_id,
        source="taobao",
        endpoint="/api/v1/ecom/taobao/search",
        shop_domain="taobao.com",
        params={"query": query, "max_pages": max_pages},
        run_id=run_id,
    )


# ── Shopify ──────────────────────────────────────────────────────────────────


async def run_shopify_scan(
    ctx: dict,
    job_id: str,
    shop_domain: str,
    max_pages: int,
    currency: str | None,
    run_id: str | None = None,
) -> None:
    store: JobStore = ctx["job_store"]
    await store.set_status(job_id, JobStatus.RUNNING)
    params = {"shop_domain": shop_domain, "max_pages": max_pages, "currency": currency}

    async with AsyncSessionLocal() as db:
        result = await engine.fetch(
            FetchRequest(
                source="shopify",
                capability=Capability.SCAN,
                params=params,
                run_id=run_id,
            ),
            db,
            ctx.get("redis_client"),
        )
        meta = result.meta
        products = result.items
        scanned_domain = meta.get("shop_domain") or shop_domain

        saved = {"total": 0, "inserted": 0, "updated": 0}
        price_points = snapshots = 0
        if products:
            saved = await ecom_crud.upsert_products(db, products)
            price_points = await ecom_crud.save_price_points(
                db,
                request_id=job_id,
                source="shopify",
                shop_domain=scanned_domain,
                products=products,
            )
            snapshots = await tracking_crud.save_snapshots(
                db, source="shopify", shop_domain=scanned_domain, products=products
            )

        await _record(
            db,
            job_id=job_id,
            endpoint="/api/v1/ecom/shopify/scan",
            params=params,
            outcome=result.outcome,
            latency_ms=result.latency_ms,
            response_data={
                "pages_fetched": meta.get("pages_fetched", 0),
                "products": len(products),
                "sample": meta.get("raw_sample", []),
            },
            error=result.error,
        )

        payload = {
            "request_id": job_id,
            "run_id": run_id,
            "shop_domain": scanned_domain,
            "outcome": str(result.outcome),
            "tier": result.tier_used,
            "currency": meta.get("currency"),
            "pages_fetched": meta.get("pages_fetched", 0),
            "products_found": len(products),
            "products_new": saved["inserted"],
            "products_updated": saved["updated"],
            "price_points_written": price_points,
            "metric_snapshots_written": snapshots,
            "latency_ms": result.latency_ms,
        }

        if is_failure(result.outcome):
            await store.set_status(job_id, JobStatus.FAILED, result=payload, error=result.error)
        else:
            await store.set_status(job_id, JobStatus.SUCCEEDED, result=payload)


# ── Bol.com ──────────────────────────────────────────────────────────────────


async def run_bol_search(
    ctx: dict,
    job_id: str,
    query: str,
    max_pages: int,
    country_path: str,
    run_id: str | None = None,
) -> None:
    store: JobStore = ctx["job_store"]
    await store.set_status(job_id, JobStatus.RUNNING)
    params = {"query": query, "max_pages": max_pages, "country_path": country_path}

    async with AsyncSessionLocal() as db:
        result = await engine.fetch(
            FetchRequest(
                source="bol",
                capability=Capability.SEARCH_KEYWORD,
                params=params,
                country=country_path,
                run_id=run_id,
            ),
            db,
            ctx.get("redis_client"),
        )
        meta = result.meta
        products = result.items

        saved = {"total": 0, "inserted": 0, "updated": 0}
        price_points = snapshots = 0
        if products:
            saved = await ecom_crud.upsert_products(db, products)
            price_points = await ecom_crud.save_price_points(
                db,
                request_id=job_id,
                source="bol",
                shop_domain="bol.com",
                products=products,
            )
            snapshots = await tracking_crud.save_snapshots(
                db, source="bol", shop_domain="bol.com", products=products
            )

        await _record(
            db,
            job_id=job_id,
            endpoint="/api/v1/ecom/bol/search",
            params=params,
            outcome=result.outcome,
            latency_ms=result.latency_ms,
            response_data={
                "pages_fetched": meta.get("pages_fetched", 0),
                "products": len(products),
                "parse_source": meta.get("parse_source"),
            },
            error=result.error,
        )

        payload = {
            "request_id": job_id,
            "run_id": run_id,
            "query": query,
            "outcome": str(result.outcome),
            "tier": result.tier_used,
            # Biết parser chạy nhánh nào là thông tin vận hành: khi 'json-ld' biến mất,
            # ta biết trước khi dữ liệu bắt đầu thiếu field.
            "parse_source": meta.get("parse_source"),
            "pages_fetched": meta.get("pages_fetched", 0),
            "products_found": len(products),
            "products_new": saved["inserted"],
            "products_updated": saved["updated"],
            "price_points_written": price_points,
            "metric_snapshots_written": snapshots,
            "latency_ms": result.latency_ms,
        }

        if is_failure(result.outcome):
            await store.set_status(job_id, JobStatus.FAILED, result=payload, error=result.error)
        else:
            await store.set_status(job_id, JobStatus.SUCCEEDED, result=payload)


# ── Reddit VOC ───────────────────────────────────────────────────────────────


async def run_reddit_voc(
    ctx: dict,
    job_id: str,
    keywords: list[str],
    timeframe: str,
    threads_per_keyword: int,
    comment_threads: int,
    comments_per_thread: int,
    sort: str = "relevance",
    subreddits: list[str] | None = None,
    min_relevance: float = 0.25,
    run_id: str | None = None,
) -> None:
    store: JobStore = ctx["job_store"]
    await store.set_status(job_id, JobStatus.RUNNING)
    params = {
        "keywords": keywords,
        "timeframe": timeframe,
        "threads_per_keyword": threads_per_keyword,
        "comment_threads": comment_threads,
        "comments_per_thread": comments_per_thread,
        "sort": sort,
        "subreddits": subreddits,
        "min_relevance": min_relevance,
    }

    async with AsyncSessionLocal() as db:
        result = await engine.fetch(
            FetchRequest(
                source="reddit",
                capability=Capability.SEARCH_KEYWORD,
                params=params,
                run_id=run_id,
            ),
            db,
            ctx.get("redis_client"),
        )
        meta = result.meta
        threads = result.items
        comments = meta.get("comments") or []
        top_subreddits = meta.get("top_subreddits") or []

        threads_saved = comments_saved = 0
        if threads:
            # `raw` giữ nguyên payload Reddit; các field còn lại đã chuẩn hoá.
            threads_saved = await voc_crud.upsert_threads(db, threads, request_id=job_id)
        if comments:
            comments_saved = await voc_crud.upsert_comments(db, comments)

        await _record(
            db,
            job_id=job_id,
            endpoint="/api/v1/voc/reddit/collect",
            params=params,
            outcome=result.outcome,
            latency_ms=result.latency_ms,
            response_data={
                "threads": threads_saved,
                "comments": comments_saved,
                "top_subreddits": top_subreddits[:5],
            },
            error=result.error,
        )

        payload = {
            "request_id": job_id,
            "run_id": run_id,
            "outcome": str(result.outcome),
            "tier": result.tier_used,
            "cost_usd": round(result.cost_usd, 4),
            "threads_saved": threads_saved,
            "comments_saved": comments_saved,
            "filter_report": meta.get("filter_report"),
            "top_subreddits": top_subreddits,
            "latency_ms": result.latency_ms,
        }

        if is_failure(result.outcome):
            await store.set_status(job_id, JobStatus.FAILED, result=payload, error=result.error)
        else:
            await store.set_status(job_id, JobStatus.SUCCEEDED, result=payload)
