"""Amazon — tier T1, mua dữ liệu qua Apify actor.

Đây là tier **chính** theo quyết định D1 của `docs/DEV-Design-Crawl-Engine.md`: bên
thứ ba đã giải bài toán anti-bot và chịu rủi ro khi Amazon đổi; ta trả tiền để không
phải cử người đi sửa parser mỗi đợt. Chọn Apify vì repo đã có sẵn client + token +
trần chi phí cho Facebook Ads — thêm nguồn ở đây không phải dựng hạ tầng mới.

`AMAZON_APIFY_ACTOR` để trong `.env` chứ không hard-code, vì docs §2 yêu cầu **chạy
thử 100 request thật với từng vendor rồi mới ký hợp đồng năm**. Đổi actor để so sánh
phải là sửa một dòng env, không phải một lần deploy.

Vì vậy `_map_item` cố tình đọc được nhiều tên trường khác nhau: mỗi actor Amazon đặt
tên một kiểu (`asin`/`ASIN`, `price`/`price.value`), và ta sẽ thử vài actor trước khi
chốt. Chấp nhận vài dòng alias ở đây để không phải sửa code mỗi lần đổi actor.
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.crawl.normalize import to_minor
from app.crawl.outcomes import Outcome
from app.services.apify_client import ApifyError, apify_client
from app.sources.amazon import normalize


class AmazonVendorAdapter:
    tier = "vendor:apify"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False
    """Vendor tự lo proxy/fingerprint — đó chính là thứ ta trả tiền để mua."""

    def is_available(self) -> bool:
        return bool(settings.apify_token and settings.amazon_apify_actor)

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params
        marketplace = p.get("marketplace") or normalize.DEFAULT_MARKETPLACE
        currency = normalize.currency_for(marketplace)
        max_items = int(p.get("max_items", 50))

        actor_input = {
            # Nhiều actor đặt tên khác nhau cho cùng một thứ. Gửi cả hai biến thể rẻ
            # hơn là đoán sai rồi tốn nguyên một lần chạy actor mới biết; actor bỏ qua
            # trường lạ chứ không lỗi.
            "search": p["query"],
            "searchQuery": p["query"],
            "domain": marketplace,
            "maxItems": max_items,
        }

        try:
            items, latency_ms = await apify_client.run_actor(
                actor_input, actor_id=settings.amazon_apify_actor
            )
        except ApifyError as exc:
            # Hết credit là "nhảy sang tier sau", khác hẳn lỗi vendor tạm thời — engine
            # đọc outcome này để quyết định, nên phân biệt ngay tại đây.
            text = str(exc).lower()
            outcome = (
                Outcome.QUOTA_EXHAUSTED
                if any(k in text for k in ("credit", "quota", "trần chi phí", "usage"))
                else Outcome.UPSTREAM_ERROR
            )
            return FetchResult(outcome=outcome, tier_used=self.tier, error=str(exc))

        products = [
            mapped
            for raw in items
            if (mapped := _map_item(raw, marketplace, currency)) is not None
        ]

        # Actor chạy xong, trả item, nhưng không map được cái nào → actor đã đổi hình
        # dạng output. Retry vô ích, phải báo động: đây đúng là ca PARSE_FAIL.
        if items and not products:
            return FetchResult(
                outcome=Outcome.PARSE_FAIL,
                tier_used=self.tier,
                latency_ms=latency_ms,
                error=f"Actor trả {len(items)} item nhưng không item nào có ASIN — output đã đổi",
                meta={"marketplace": marketplace, "sample": items[:2]},
            )

        return FetchResult(
            outcome=Outcome.OK if products else Outcome.EMPTY,
            items=products,
            tier_used=self.tier,
            latency_ms=latency_ms,
            meta={"marketplace": marketplace, "actor": settings.amazon_apify_actor},
        )


def _first(data: dict, *keys: str) -> Any:
    """Giá trị đầu tiên không rỗng trong các tên trường có thể có."""
    for k in keys:
        v = data.get(k)
        if v not in (None, "", []):
            return v
    return None


def _map_item(raw: dict, marketplace: str, currency: str) -> dict | None:
    asin = _first(raw, "asin", "ASIN", "productAsin", "id")
    if not asin:
        return None
    asin = str(asin).strip()

    price_raw = _first(raw, "price", "currentPrice", "priceValue")
    if isinstance(price_raw, dict):
        price_raw = _first(price_raw, "value", "amount", "current")
    price_minor = to_minor(price_raw, currency) if price_raw is not None else None

    rating_raw = _first(raw, "stars", "rating", "averageRating")
    try:
        rating = float(rating_raw) if rating_raw is not None else None
    except (TypeError, ValueError):
        rating = None

    review_raw = _first(raw, "reviewsCount", "reviewCount", "numberOfReviews", "totalReviews")
    try:
        review_count = int(review_raw) if review_raw is not None else None
    except (TypeError, ValueError):
        review_count = None

    images = _first(raw, "images", "imageUrls", "galleryImages")
    if isinstance(images, str):
        images = [images]
    elif not isinstance(images, list):
        thumb = _first(raw, "thumbnailImage", "image", "imageUrl")
        images = [thumb] if thumb else None

    return {
        "source": "amazon",
        "shop_domain": marketplace,
        "external_id": asin,
        "title": str(_first(raw, "title", "name", "productTitle") or ""),
        "url": str(_first(raw, "url", "link", "productUrl") or f"https://www.{marketplace}/dp/{asin}"),
        "brand": _first(raw, "brand", "manufacturer"),
        "product_type": _first(raw, "category", "productCategory"),
        "currency": _first(raw, "currency") or currency,
        "price_min_minor": price_minor,
        "price_max_minor": price_minor,
        "rating": rating,
        "review_count": review_count,
        "sales_volume": None,  # Amazon không công bố — xem docs §2.2
        "bestseller_rank": _int_or_none(_first(raw, "bestSellersRank", "bsr", "salesRank")),
        "available": _first(raw, "inStock", "available"),
        "seller": _first(raw, "seller", "sellerName", "soldBy"),
        "is_sponsored": _first(raw, "isSponsored", "sponsored"),
        "image_refs": images,
        "raw": raw,
        "variants": (
            [
                {
                    "external_variant_id": asin,
                    "variant_title": None,
                    "sku": asin,
                    "price_minor": price_minor,
                    "compare_at_minor": None,
                    "available": None,
                }
            ]
            if price_minor is not None
            else []
        ),
    }


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, dict):
        value = _first(value, "rank", "value")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
