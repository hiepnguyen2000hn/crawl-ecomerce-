"""1688 — tier T1 thứ hai, mua dữ liệu qua Apify actor.

VÌ SAO THÊM MỘT TIER VENDOR NỮA

`vendor:aggregator` (aggregator Trung Quốc) vẫn là tier chính theo thiết kế, nhưng nó
đòi ký hợp đồng và trả tiền theo năm — chưa có key thì `is_available()` trả False và
nguồn 1688 rơi thẳng xuống tier browser, vốn gần như chắc chắn `BLOCKED` vì thiếu
residential Trung Quốc đại lục. Kết quả: 1688 chưa chạy được lần nào.

Actor Apify lấp đúng khoảng trống đó: trả tiền theo lượt, không cam kết, đủ để có dữ
liệu thật mà đánh giá xem có đáng ký aggregator hay không — đúng tinh thần docs §2
("chạy thử 100 request thật rồi mới ký hợp đồng năm").

Thứ tự trong registry vì vậy là: aggregator (nếu đã mua) → apify → browser.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.crawl.normalize import to_minor
from app.crawl.outcomes import Outcome
from app.services.apify_client import ApifyError, apify_client

CURRENCY = "CNY"
_TAG_RE = re.compile(r"<[^>]+>")
_NUM_RE = re.compile(r"[\d.]+")


class Alibaba1688ApifyAdapter:
    tier = "vendor:apify"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False
    """Vendor tự lo proxy Trung Quốc — đó chính là thứ ta trả tiền để mua."""

    def is_available(self) -> bool:
        return bool(settings.apify_token and settings.alibaba_1688_apify_actor)

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params
        max_items = int(p.get("max_items", 50))
        actor_input = {
            "keywords": [p["query"]],
            "maxItems": max_items,
            # 1688 trả tối đa 50 offer mỗi trang; xin đủ số trang để phủ `max_items`.
            "maxPagesPerKeyword": max(1, -(-max_items // 50)),
            "includeDetails": bool(p.get("include_details", False)),
        }

        try:
            items, latency_ms = await apify_client.run_actor(
                actor_input, actor_id=settings.alibaba_1688_apify_actor
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

        if items and not products:
            return FetchResult(
                outcome=Outcome.PARSE_FAIL,
                tier_used=self.tier,
                latency_ms=latency_ms,
                error=f"Actor trả {len(items)} item nhưng không item nào có offerId — output đã đổi",
                meta={"actor": settings.alibaba_1688_apify_actor, "sample": items[:2]},
            )

        return FetchResult(
            outcome=Outcome.OK if products else Outcome.EMPTY,
            items=products,
            tier_used=self.tier,
            latency_ms=latency_ms,
            meta={"actor": settings.alibaba_1688_apify_actor, "parse_source": "vendor:apify"},
        )


def _first(data: dict, *keys: str) -> Any:
    for k in keys:
        v = data.get(k)
        if v not in (None, "", []):
            return v
    return None


def _plain(value: Any) -> str | None:
    """Bỏ thẻ HTML khỏi tiêu đề.

    Actor trả nguyên tiêu đề của trang kết quả, mà 1688 bọc từ khoá khớp trong
    `<font color=red>…</font>` để bôi đỏ. Không bóc thì tiêu đề trong DB và file Excel
    thành `跨境创意河豚<font color=red>加湿器</font>usb…` — vẫn đọc được nên rất dễ lọt
    qua review, rồi hỏng ở mọi chỗ dùng lại (so khớp AI, xuất báo cáo, tìm kiếm).
    """
    if value is None:
        return None
    t = _TAG_RE.sub("", str(value)).strip()
    return t or None


def _vc(value: Any, limit: int = 255) -> str | None:
    t = _plain(value)
    return None if t is None else (t if len(t) <= limit else t[: limit - 1] + "…")


def _sold(value: Any) -> int | None:
    """`成交1笔` / `成交1000+笔` → số nguyên.

    Đây là trường ĐÁNG GIÁ NHẤT của 1688: theo docs §2.2, Amazon và Bol.com đều không
    công bố sản lượng bán, nên `S2.2` của chúng luôn rỗng. Bỏ qua nó vì ngại parse chữ
    Trung là vứt đúng thứ khiến nguồn này có giá trị.

    `1000+` làm tròn xuống 1000 — con số sàn, không phải ước lượng. Thà báo thiếu còn
    hơn bịa thêm.
    """
    if value is None:
        return None
    m = _NUM_RE.search(str(value))
    if not m:
        return None
    try:
        return int(float(m.group()))
    except ValueError:
        return None


def _price(value: Any) -> float | None:
    if value is None:
        return None
    m = _NUM_RE.search(str(value))
    try:
        return float(m.group()) if m else None
    except ValueError:
        return None


def _specs(raw: dict) -> str | None:
    """Gom các chỉ số nhà cung cấp mà schema `ecom_products` chưa có cột riêng.

    MOQ, tỉ lệ mua lại, thâm niên và nơi đặt xưởng là bốn thứ đội mua hàng nhìn đầu
    tiên. Bảng `supplier_listings` trong thiết kế chưa từng được tạo (1688 dùng chung
    `ecom_products`), nên tạm gom vào đây thay vì để mất.
    """
    parts = []
    moq, unit = raw.get("minOrderQuantity"), raw.get("unit")
    if moq:
        parts.append(f"MOQ: {moq}{unit or ''}")
    if raw.get("repurchaseRate"):
        parts.append(f"Tỉ lệ mua lại: {raw['repurchaseRate']}")
    if raw.get("sellerTenure"):
        parts.append(f"Thâm niên NCC: {raw['sellerTenure']}")
    if raw.get("location"):
        parts.append(f"Nơi đặt: {raw['location']}")
    badges = raw.get("supplierBadges")
    if isinstance(badges, list) and badges:
        parts.append("Chứng nhận: " + ", ".join(str(b) for b in badges))
    return "\n".join(parts) or None


def _map_item(raw: dict) -> dict | None:
    offer_id = _first(raw, "offerId", "id", "productId")
    if not offer_id:
        return None
    offer_id = str(offer_id).strip()

    price_from = _price(_first(raw, "priceFrom", "price", "minPrice"))
    price_to = _price(_first(raw, "priceTo", "maxPrice"))
    price_min = to_minor(price_from, CURRENCY) if price_from is not None else None
    price_max = to_minor(price_to, CURRENCY) if price_to is not None else price_min

    image = _first(raw, "image", "imageUrl", "mainImage")
    images = [image] if isinstance(image, str) else (image if isinstance(image, list) else None)

    return {
        "source": "alibaba_1688",
        "shop_domain": "1688.com",
        "external_id": offer_id,
        "title": _plain(_first(raw, "title", "subject", "name")) or "",
        # Actor trả link bản mobile (`m.1688.com`); dựng lại link desktop cho thống nhất
        # với tier aggregator và để người mua hàng mở được trên máy tính.
        "url": f"https://detail.1688.com/offer/{offer_id}.html",
        "brand": None,
        "product_type": None,
        "currency": CURRENCY,
        "price_min_minor": price_min,
        "price_max_minor": price_max,
        # 1688 hiển thị điểm của NHÀ CUNG CẤP, không phải của sản phẩm — để None còn hơn
        # nhét điểm shop vào cột rating sản phẩm (giữ đúng quy ước của tier aggregator).
        "rating": None,
        "review_count": None,
        "sales_volume": _sold(_first(raw, "soldCount", "sold", "tradeCount")),
        "available": None,
        "seller": _vc(_first(raw, "sellerCompany", "companyName", "seller")),
        "is_sponsored": None,
        "image_refs": images,
        "raw": raw,
        # KHÔNG nhét `_specs` vào đây: `upsert_products` đẩy nguyên dict xuống
        # `pg_insert(EcomProduct)`, khoá lạ là vỡ câu lệnh. Bốn chỉ số nhà cung cấp nằm
        # sẵn trong `raw`; chỗ nào cần hiển thị thì gọi `_specs(raw)`.
        "variants": (
            [
                {
                    "external_variant_id": offer_id,
                    "variant_title": None,
                    "sku": None,
                    "price_minor": price_min,
                    "compare_at_minor": price_max if price_max != price_min else None,
                    "available": None,
                }
            ]
            if price_min is not None
            else []
        ),
    }
