"""Bol.com — tier T1, mua dữ liệu qua Apify actor.

VÌ SAO BOL CŨNG ĐI VENDOR-FIRST, KHÁC VỚI PHÂN TÍCH BAN ĐẦU

Tài liệu thiết kế xếp Bol vào nhóm "anti-bot nhẹ, tự scrape + proxy datacenter là đủ".
Thực tế đo được: IP văn phòng bị trả **403 ngay trang đầu, không captcha** — chặn theo
danh tiếng IP chứ không phải theo fingerprint. Scraper tự viết vẫn đúng, nhưng nó chỉ
chạy được từ một IP sạch, mà `watchlist_tick` chạy mỗi giờ thì không thể phụ thuộc vào
việc có người ngồi ở một đường mạng cụ thể.

Vendor giải đúng bài toán đó: bên thứ ba lo IP, ta không phải mua proxy NL/BE riêng cho
một nguồn. Ở quy mô hiện tại (~1.100 SP/tháng) giá vendor còn **rẻ hơn** thuê proxy
tháng — điểm hoà vốn khoảng 1.500 SP/tháng.

Tier `browser` (scraper tự viết) giữ nguyên phía sau làm dự phòng: chạy từ IP sạch thì
nó miễn phí, và mỗi lần nó trả `BLOCKED` là một dòng bằng chứng trong `crawl_attempts`.

`BOL_APIFY_ACTOR` để trong `.env` vì hai actor ứng viên khác hẳn nhau về đánh đổi:

  studio-amba~bol-scraper   giá công khai $0,005/run + $0,002/item — chỉ có listing,
                            KHÔNG có mô tả (đúng lỗ hổng ở lộ trình 0b)
  abotapi~bol-com-scraper   có `fetchDetails` + `maxReviews` (lấp được lỗ hổng đó) và
                            `incrementalMode` cho job theo lịch, nhưng **giá sự kiện
                            không công khai** — phải chạy thử mới biết

Vì vậy `_build_input` gửi trường của cả hai schema, và `_map_item` đọc được nhiều tên
trường khác nhau: đổi actor để so sánh phải là sửa một dòng env, không phải một lần
deploy. Cùng lý do với adapter Amazon.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.crawl.normalize import to_minor
from app.crawl.outcomes import Outcome
from app.services.apify_client import ApifyError, apify_client

DEFAULT_CURRENCY = "EUR"
BASE = "https://www.bol.com"
_ID_RE = re.compile(r"/p/[^/]*/(\d{6,})")


class BolVendorAdapter:
    tier = "vendor:apify"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False
    """Vendor tự lo IP — đó chính là thứ ta trả tiền để mua, và là lý do tier này tồn tại."""

    def is_available(self) -> bool:
        return bool(settings.apify_token and settings.bol_apify_actor)

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params
        max_items = int(p.get("max_items", 50))
        actor_input = _build_input(
            p["query"],
            max_items,
            bool(p.get("fetch_details", False)),
            p.get("country_path", "/nl/nl"),
        )

        try:
            items, latency_ms = await apify_client.run_actor(
                actor_input, actor_id=settings.bol_apify_actor
            )
        except ApifyError as exc:
            text = str(exc).lower()
            outcome = (
                Outcome.QUOTA_EXHAUSTED
                if any(k in text for k in ("credit", "quota", "trần chi phí", "usage"))
                else Outcome.UPSTREAM_ERROR
            )
            return FetchResult(outcome=outcome, tier_used=self.tier, error=str(exc))

        products = [m for raw in items if (m := _map_item(raw)) is not None]

        # Actor chạy xong, trả item, nhưng không map được cái nào → output đã đổi hình
        # dạng. Retry vô ích, phải báo động: đúng ca PARSE_FAIL.
        if items and not products:
            return FetchResult(
                outcome=Outcome.PARSE_FAIL,
                tier_used=self.tier,
                latency_ms=latency_ms,
                error=f"Actor trả {len(items)} item nhưng không item nào có mã sản phẩm — output đã đổi",
                meta={"actor": settings.bol_apify_actor, "sample": items[:2]},
            )

        return FetchResult(
            outcome=Outcome.OK if products else Outcome.EMPTY,
            items=products,
            tier_used=self.tier,
            latency_ms=latency_ms,
            meta={"actor": settings.bol_apify_actor, "parse_source": "vendor:apify"},
        )


def _build_input(
    query: str, max_items: int, fetch_details: bool, country_path: str
) -> dict[str, Any]:
    """Input thoả schema của cả BA actor ứng viên cùng lúc.

    Ba actor đặt tên cùng một thứ theo ba kiểu — bài học phải trả tiền mới biết: lần đầu
    chỉ gửi `searchQuery`/`searchTerms`, actor `piotrv1001` đọc `searchQueries` nên chạy
    xong trả 0 item (`EMPTY`), tốn phí khởi động mà không có dữ liệu.

    Schema của chúng không khoá `additionalProperties` nên gửi thừa là an toàn: actor nào
    không biết trường nào thì bỏ qua trường đó. Rẻ hơn nhiều so với viết ba adapter rồi
    phải sửa cả ba mỗi lần đổi gì.
    """
    # '/nl/nl' → 'nl', '/be/nl' → 'be' — cùng catalog, khác giá.
    country = (country_path or "/nl/nl").strip("/").split("/")[0] or "nl"
    return {
        # studio-amba~bol-scraper
        "searchQuery": query,
        "maxResults": max_items,
        # abotapi~bol-com-scraper — `mode` là trường BẮT BUỘC của schema này
        "mode": "search",
        "searchTerms": [query],
        "maxListings": max_items,
        "fetchDetails": fetch_details,
        # piotrv1001~bol-listings-scraper
        "searchQueries": [query],
        "maxItems": max_items,
        "country": country,
        "includeProductDetails": fetch_details,
    }


def _first(data: dict, *keys: str) -> Any:
    for k in keys:
        v = data.get(k)
        if v not in (None, "", []):
            return v
    return None


def _as_text(value: Any) -> str | None:
    """Ép về chuỗi cho các cột VARCHAR — xem cùng hàm bên adapter Amazon.

    Ở Amazon, `seller` về dạng object đã làm asyncpg ném `DataError` và mất trắng cả lô
    sản phẩm dù actor đã chạy xong và đã tính tiền. Actor Bol chưa được kiểm chứng nên
    ép kiểu ngay từ đầu, đừng đợi mất một lô nữa mới biết.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _as_text(_first(value, "name", "sellerName", "displayName", "title", "id"))
    if isinstance(value, (list, tuple)):
        parts = [t for v in value if (t := _as_text(v))]
        return " > ".join(parts) or None
    return str(value)


def _vc(value: Any, limit: int = 255) -> str | None:
    """Cắt cho vừa cột VARCHAR(255).

    Bản ghi chi tiết của actor trả `category` là MẢNG breadcrumb — nối lại thành chuỗi
    720 ký tự, vượt cột `ecom_products.product_type` và làm asyncpg ném
    `StringDataRightTruncationError`, mất trắng cả lô dù actor đã chạy xong và đã tính
    tiền. Cùng loại lỗi với `seller` dạng object bên Amazon: dữ liệu vendor không có
    nghĩa vụ vừa schema của ta, nên chỗ tiếp giáp phải tự lo.
    """
    t = _as_text(value)
    if t is None:
        return None
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _extract_id(url: str) -> str | None:
    m = _ID_RE.search(url or "")
    return m.group(1) if m else None


def _num(value: Any) -> float | None:
    if isinstance(value, dict):
        value = _first(value, "value", "amount", "current", "price")
    try:
        return float(str(value).replace(",", ".")) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


# Đuôi cố định của thẻ người bán trên trang Bol. Actor bê nguyên text của thẻ vào
# `sellerHint`, nên chuỗi có dạng "{Tên}{Tên}Wat je kan verwachten".
_SELLER_TAIL = "Wat je kan verwachten"


def _seller(raw: dict) -> str | None:
    """Tên người bán, làm sạch `sellerHint` của actor `piotrv1001`.

    Actor không có trường `seller` sạch — nó gộp nguyên text thẻ bán hàng. Đo trên 20
    sản phẩm thật: 19/20 có `sellerHint`, tất cả theo đúng một khuôn (tên lặp đôi rồi
    tới câu cố định), riêng 'bol' thì đã sạch sẵn.

    Không làm bước này thì cột `seller` nhận "Vivid GreenVivid GreenWat je kan
    verwachten" — tệ hơn để trống, vì nó trông như dữ liệu thật và sẽ trôi vào báo cáo.
    Hai phép biến đổi dưới đây đều xác định được (cắt đuôi cố định, gấp đôi chính xác),
    không phải đoán mò; không khớp khuôn thì trả nguyên văn chứ không cố sửa.
    """
    v = _as_text(_first(raw, "seller", "sellerName", "soldBy", "merchant", "sellerHint"))
    if not v:
        return None
    if v.endswith(_SELLER_TAIL):
        v = v[: -len(_SELLER_TAIL)].strip()
    n = len(v)
    if n and n % 2 == 0 and v[: n // 2] == v[n // 2 :]:
        v = v[: n // 2]
    return v.strip() or None


def _map_item(raw: dict) -> dict | None:
    url = _as_text(_first(raw, "url", "productUrl", "link", "productPageUrl")) or ""
    external_id = (
        # `productGroupId` là tên của bản ghi CHI TIẾT; listing dùng `productId`.
        _as_text(_first(raw, "productId", "productGroupId", "ean", "id", "sku", "bolProductId"))
        or _extract_id(url)
    )
    if not external_id:
        return None
    external_id = str(external_id).strip()

    currency = _as_text(_first(raw, "currency", "priceCurrency")) or DEFAULT_CURRENCY
    if len(currency) != 3:  # actor có thể trả ký hiệu "€" — cột DB là VARCHAR(3)
        currency = DEFAULT_CURRENCY

    # Listing trả một giá (`price`); bản chi tiết trả KHOẢNG giá theo các biến thể
    # (`lowPrice`/`highPrice`) — giữ cả hai đầu thay vì ép về một số.
    price = _num(_first(raw, "price", "currentPrice", "priceValue", "salePrice", "lowPrice"))
    price_high = _num(_first(raw, "highPrice")) 
    price_minor = to_minor(price, currency) if price is not None else None
    price_max_minor = to_minor(price_high, currency) if price_high is not None else price_minor
    was = _num(_first(raw, "listPrice", "originalPrice", "compareAtPrice", "strikePrice"))
    was_minor = to_minor(was, currency) if was is not None else None

    images = _first(raw, "images", "imageUrls", "galleryImages")
    if isinstance(images, str):
        images = [images]
    elif not isinstance(images, list):
        thumb = _as_text(_first(raw, "image", "imageUrl", "thumbnail", "thumbnailImage"))
        images = [thumb] if thumb else None

    rating = _num(_first(raw, "rating", "averageRating", "stars", "ratingValue"))

    return {
        "source": "bol",
        "shop_domain": "bol.com",
        "external_id": external_id,
        "title": _as_text(_first(raw, "title", "name", "productTitle")) or "",
        "url": url if url.startswith("http") else f"{BASE}{url}" if url else
        f"{BASE}/nl/nl/p/-/{external_id}/",
        "brand": _vc(_first(raw, "brand", "brandName", "manufacturer")),
        "product_type": _vc(_first(raw, "category", "categoryPath", "breadCrumbs")),
        "currency": currency,
        "price_min_minor": price_minor,
        "price_max_minor": price_max_minor,
        "rating": rating,
        "review_count": _int_or_none(_first(raw, "reviewCount", "reviewsCount", "numberOfReviews")),
        "sales_volume": None,  # Bol.com không công bố — xem docstring model EcomProduct
        "bestseller_rank": _int_or_none(_first(raw, "rank", "bestsellerRank", "salesRank")),
        "available": _first(raw, "inStock", "available", "isAvailable"),
        "seller": _vc(_seller(raw)),
        "is_sponsored": _first(raw, "isSponsored", "sponsored"),
        "image_refs": images,
        "raw": raw,
        "variants": (
            [
                {
                    "external_variant_id": external_id,
                    "variant_title": None,
                    "sku": _as_text(_first(raw, "sku", "ean")) or external_id,
                    "price_minor": price_minor,
                    "compare_at_minor": was_minor,
                    "available": _first(raw, "inStock", "available", "isAvailable"),
                }
            ]
            if price_minor is not None
            else []
        ),
    }
