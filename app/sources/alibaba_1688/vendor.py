"""1688 — tier T1, mua dữ liệu qua aggregator. **Đây là tier chính.**

Đây cũng là adapter duy nhất hiện khai `SEARCH_IMAGE`: tìm nguồn hàng bằng ảnh là
yêu cầu bắt buộc của SRS Bước 4.1, và `item_search_img` của aggregator là đường khả
thi duy nhất — tự scrape thì phải mô phỏng cả luồng upload ảnh sau lớp đăng nhập.
"""

from __future__ import annotations

import re
from typing import Any

from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.crawl.normalize import to_minor
from app.crawl.outcomes import Outcome
from app.services import alibaba_aggregator as agg

CURRENCY = "CNY"


class Alibaba1688VendorAdapter:
    tier = "vendor:aggregator"
    capabilities = {Capability.SEARCH_KEYWORD, Capability.SEARCH_IMAGE}
    needs_identity = False
    """Vendor tự lo proxy TQ, cookie đăng nhập và device token — đó là thứ ta mua."""

    def is_available(self) -> bool:
        return agg.is_configured()

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params

        if req.capability is Capability.SEARCH_IMAGE:
            api_name = "item_search_img"
            params = {"imgid": p.get("image_id") or "", "imgurl": p.get("image_url") or ""}
        else:
            api_name = "item_search"
            params = {"q": p["query"], "page": p.get("page", 1)}

        result = await agg.call(api_name, params)

        if result.outcome not in (Outcome.OK, Outcome.EMPTY):
            return FetchResult(
                outcome=result.outcome,
                tier_used=self.tier,
                latency_ms=result.latency_ms,
                error=result.error,
            )

        rows = [r for raw in result.items if (r := _map_item(raw)) is not None]

        # Gọi được, có item, nhưng không map nổi cái nào → vendor đã đổi hình dạng
        # output. Retry vô ích; phải báo động.
        if result.items and not rows:
            return FetchResult(
                outcome=Outcome.PARSE_FAIL,
                tier_used=self.tier,
                latency_ms=result.latency_ms,
                error=f"Vendor trả {len(result.items)} item nhưng không item nào có offer id",
                meta={"sample": result.items[:2]},
            )

        return FetchResult(
            outcome=Outcome.OK if rows else Outcome.EMPTY,
            items=rows,
            tier_used=self.tier,
            latency_ms=result.latency_ms,
            meta={"api_name": api_name},
        )


def _first(data: dict, *keys: str) -> Any:
    for k in keys:
        v = data.get(k)
        if v not in (None, "", []):
            return v
    return None


def _map_item(raw: dict) -> dict | None:
    offer_id = _first(raw, "num_iid", "offerId", "item_id", "id")
    if offer_id is None:
        return None
    offer_id = str(offer_id).strip()

    price = _first(raw, "price", "orginal_price", "promotion_price")
    price_minor = to_minor(price, CURRENCY) if price is not None else None

    images = _first(raw, "item_imgs", "images", "pic_url")
    if isinstance(images, list):
        images = [
            (i.get("url") if isinstance(i, dict) else i) for i in images
        ]
        images = [i for i in images if i]
    elif isinstance(images, str):
        images = [images]
    else:
        images = None

    return {
        "source": "alibaba_1688",
        "shop_domain": "1688.com",
        "external_id": offer_id,
        "title": str(_first(raw, "title", "subject", "name") or ""),
        "url": str(
            _first(raw, "detail_url", "url") or f"https://detail.1688.com/offer/{offer_id}.html"
        ),
        "brand": _first(raw, "brand"),
        "product_type": _first(raw, "cat_name", "category"),
        "currency": CURRENCY,
        "price_min_minor": price_minor,
        "price_max_minor": price_minor,
        "rating": None,
        "review_count": _int_or_none(_first(raw, "comment_count", "reviews")),
        # 1688 công bố số đã bán — trường hiếm, xem docs §2.2.
        "sales_volume": _int_or_none(_first(raw, "sales", "sale_num", "volume")),
        "available": None,
        "seller": _first(raw, "nick", "seller_nick", "company", "shop_title"),
        "is_sponsored": None,
        "image_refs": images,
        "raw": raw,
        "variants": (
            [
                {
                    "external_variant_id": offer_id,
                    "variant_title": None,
                    "sku": None,
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
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
