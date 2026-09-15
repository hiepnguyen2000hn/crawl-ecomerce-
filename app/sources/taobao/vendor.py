"""Taobao — tier T1, mua dữ liệu qua aggregator. **Đây là tier chính.**

Cùng nhà cung cấp với 1688 (`app/services/alibaba_aggregator.py`), chỉ khác `platform`
và tên trường trả về — nên phần gọi HTTP dùng chung, phần map để riêng ở đây.
"""

from __future__ import annotations

import re
from typing import Any

from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.crawl.normalize import to_minor
from app.crawl.outcomes import Outcome
from app.services import alibaba_aggregator as agg

CURRENCY = "CNY"


class TaobaoVendorAdapter:
    tier = "vendor:aggregator"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False

    def is_available(self) -> bool:
        return agg.is_configured()

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params
        result = await agg.call(
            "item_search", {"q": p["query"], "page": p.get("page", 1)}, platform="taobao"
        )

        if result.outcome not in (Outcome.OK, Outcome.EMPTY):
            return FetchResult(
                outcome=result.outcome,
                tier_used=self.tier,
                latency_ms=result.latency_ms,
                error=result.error,
            )

        rows = [r for raw in result.items if (r := _map_item(raw)) is not None]

        if result.items and not rows:
            return FetchResult(
                outcome=Outcome.PARSE_FAIL,
                tier_used=self.tier,
                latency_ms=result.latency_ms,
                error=f"Vendor trả {len(result.items)} item nhưng không item nào có id",
                meta={"sample": result.items[:2]},
            )

        return FetchResult(
            outcome=Outcome.OK if rows else Outcome.EMPTY,
            items=rows,
            tier_used=self.tier,
            latency_ms=result.latency_ms,
        )


def _first(data: dict, *keys: str) -> Any:
    for k in keys:
        v = data.get(k)
        if v not in (None, "", []):
            return v
    return None


def _map_item(raw: dict) -> dict | None:
    item_id = _first(raw, "num_iid", "item_id", "nid", "id")
    if item_id is None:
        return None
    item_id = str(item_id).strip()

    price = _first(raw, "price", "promotion_price", "orginal_price")
    price_minor = to_minor(price, CURRENCY) if price is not None else None

    images = _first(raw, "item_imgs", "images", "pic_url")
    if isinstance(images, list):
        images = [i.get("url") if isinstance(i, dict) else i for i in images]
        images = [i for i in images if i]
    elif isinstance(images, str):
        images = [images]
    else:
        images = None

    return {
        "source": "taobao",
        "shop_domain": "taobao.com",
        "external_id": item_id,
        "title": str(_first(raw, "title", "name") or ""),
        "url": str(
            _first(raw, "detail_url", "url") or f"https://item.taobao.com/item.htm?id={item_id}"
        ),
        "brand": _first(raw, "brand"),
        "product_type": _first(raw, "cat_name", "category"),
        "currency": CURRENCY,
        "price_min_minor": price_minor,
        "price_max_minor": price_minor,
        "rating": None,
        "review_count": _int_or_none(_first(raw, "comment_count", "reviews")),
        "sales_volume": _int_or_none(_first(raw, "sales", "sale_num", "volume")),
        "available": None,
        "seller": _first(raw, "nick", "seller_nick", "shop_title"),
        "is_sponsored": None,
        "image_refs": images,
        "raw": raw,
        "variants": (
            [
                {
                    "external_variant_id": item_id,
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
