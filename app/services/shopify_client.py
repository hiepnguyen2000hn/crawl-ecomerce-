"""Connector Shopify — endpoint công khai `/products.json`.

Đây là nguồn dễ nhất trong toàn bộ F2: công khai, không cần auth, không cần proxy,
miễn phí, và giá **chính xác 100%** (lấy thẳng từ store chứ không suy đoán).
Phục vụ SRS Bước 3 — phân tích giá đối thủ.

Giới hạn đã biết:
  - Store có thể tắt endpoint này → 404 (NOT_AVAILABLE)
  - Store đặt mật khẩu → redirect /password (BLOCKED)
  - Một số store sau Cloudflare → cần proxy (BLOCKED)
  - Bị throttle → HTTP 430, khác với 429 thông thường
  - `/products.json` KHÔNG trả mã tiền tệ → phải hỏi riêng `/meta.json`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.crawl import http
from app.crawl.normalize import normalize_domain, to_minor
from app.crawl.outcomes import Outcome

PAGE_LIMIT = 250  # trần Shopify cho phép mỗi trang


@dataclass
class ShopifyScanResult:
    outcome: Outcome
    shop_domain: str
    currency: str | None = None
    products: list[dict] = field(default_factory=list)
    pages_fetched: int = 0
    latency_ms: int = 0
    error: str | None = None
    raw_sample: list[dict] = field(default_factory=list)


async def detect_currency(shop_domain: str) -> str | None:
    """`/products.json` không có mã tiền tệ; `/meta.json` thì có.

    Không phải store nào cũng bật `/meta.json` → trả None và để caller truyền tay.
    Thà để trống còn hơn đoán bừa EUR rồi so nhầm với ngưỡng COGS_max.
    """
    resp = await http.fetch(f"https://{shop_domain}/meta.json")
    if resp.outcome is not None:
        return None
    data = resp.json()
    if isinstance(data, dict):
        cur = data.get("currency")
        if isinstance(cur, str) and len(cur) == 3:
            return cur.upper()
    return None


async def scan_store(
    shop_domain: str,
    *,
    max_pages: int = 10,
    currency: str | None = None,
) -> ShopifyScanResult:
    """Quét toàn bộ catalog công khai của một store Shopify."""
    domain = normalize_domain(shop_domain)
    result = ShopifyScanResult(outcome=Outcome.OK, shop_domain=domain)

    result.currency = currency or await detect_currency(domain)

    collected: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = await http.fetch(
            f"https://{domain}/products.json",
            params={"limit": PAGE_LIMIT, "page": page},
        )
        result.latency_ms += resp.latency_ms

        if resp.outcome is not None:
            # Đã lấy được vài trang rồi mới gặp lỗi → giữ phần đã có, đánh dấu outcome.
            result.outcome = resp.outcome
            result.error = resp.error or f"HTTP {resp.status_code} tại trang {page}"
            break

        payload = resp.json()
        if not isinstance(payload, dict) or "products" not in payload:
            # 200, không bị chặn, nhưng cấu trúc không như mong đợi → nguồn đã đổi.
            result.outcome = Outcome.PARSE_FAIL
            result.error = "Phản hồi 200 nhưng không có khoá 'products' — cấu trúc đã đổi"
            break

        batch = payload.get("products") or []
        if not batch:
            break

        collected.extend(batch)
        result.pages_fetched = page

        if len(batch) < PAGE_LIMIT:
            break  # trang cuối

    result.raw_sample = collected[:3]

    if result.outcome in (Outcome.OK, Outcome.PARSE_FAIL) and collected:
        result.products = [_normalize(p, domain, result.currency) for p in collected]
        result.outcome = Outcome.OK
    elif result.outcome is Outcome.OK and not collected:
        result.outcome = Outcome.EMPTY

    return result


def _normalize(product: dict[str, Any], shop_domain: str, currency: str | None) -> dict:
    """Payload Shopify → hình dạng chung của `ecom_products` + `ecom_price_points`.

    Giá nằm ở tầng variant chứ không phải product, nên product giữ dải min–max
    còn từng variant thành một điểm giá riêng.
    """
    variants = product.get("variants") or []
    prices: list[int] = []
    normalized_variants: list[dict] = []

    for v in variants:
        minor = to_minor(v.get("price"), currency)
        if minor is None:
            continue
        prices.append(minor)
        normalized_variants.append(
            {
                "external_variant_id": str(v.get("id")),
                "variant_title": v.get("title"),
                "sku": v.get("sku") or None,
                "price_minor": minor,
                "compare_at_minor": to_minor(v.get("compare_at_price"), currency),
                "available": v.get("available"),
            }
        )

    handle = product.get("handle")
    images = [img.get("src") for img in (product.get("images") or []) if img.get("src")]

    return {
        "source": "shopify",
        "shop_domain": shop_domain,
        "external_id": str(product.get("id")),
        "title": product.get("title") or "",
        "url": f"https://{shop_domain}/products/{handle}" if handle else None,
        "brand": product.get("vendor") or None,
        "product_type": product.get("product_type") or None,
        "currency": currency,
        "price_min_minor": min(prices) if prices else None,
        "price_max_minor": max(prices) if prices else None,
        # Shopify không công bố rating / số đã bán qua endpoint công khai.
        "rating": None,
        "review_count": None,
        "sales_volume": None,
        "available": any(v.get("available") for v in variants) if variants else None,
        # Store chính là người bán; catalog riêng nên không có vị trí quảng cáo.
        "seller": shop_domain,
        "is_sponsored": False,
        "image_refs": images or None,
        "raw": product,
        "variants": normalized_variants,
    }
