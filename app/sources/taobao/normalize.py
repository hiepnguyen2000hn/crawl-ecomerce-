"""Đọc trang tìm kiếm Taobao → hình dạng chuẩn của `ecom_products`.

Thuần hàm, không I/O. Dùng chung bảng `ecom_products` với `source='taobao'` — lý do
giống 1688, xem docstring `app/sources/alibaba_1688/normalize.py`.

CHIẾN LƯỢC PARSE:
  1. **Ưu tiên object JS `g_page_config`.** Taobao dựng toàn bộ danh sách từ biến này;
     HTML trả về hầu như không có nội dung sản phẩm nào để mà đọc.
  2. Dự phòng: gom link `item.taobao.com/item.htm?id=<id>` trong DOM, neo vào tham số
     `id` của URL chứ không vào class.
  3. Không ra gì mà trang 200 và không có dấu hiệu chặn → `PARSE_FAIL`.

Taobao **có** công bố số người đã mua ("123人付款") — trường `view_sales`. Đây là một
trong số rất ít nguồn điền được `sales_volume`, xem docs §2.2.

⚠️ Tên biến và tên trường ở đây cần kiểm chứng bằng HTML thật — dùng
`GET /api/v1/ecom/taobao/probe` trước khi chạy job hàng loạt.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from app.crawl.html_json import extract_js_object, walk
from app.crawl.normalize import parse_decimal, to_minor

CURRENCY = "CNY"
SEARCH_BASE = "https://s.taobao.com/search"

#: Taobao phân trang theo *offset* chứ không theo số trang, mỗi trang 44 mục.
PAGE_SIZE = 44

_DATA_VARS = ("g_page_config", "window.g_page_config", "__INITIAL_DATA__")

#: Khoá chứa mảng sản phẩm bên trong `g_page_config`.
_LIST_KEYS = ("auctions", "itemlist", "items", "list")

_ITEM_ID_RE = re.compile(r"[?&]id=(\d{6,})")

#: "123人付款" / "1.2万人付款" — số người đã mua.
_SALES_RE = re.compile(r"([\d.]+)\s*(万)?\s*人")

_NO_RESULT_MARKERS = ("没有找到", "没有找到相关", "抱歉", "no results")


def search_url(query: str, page: int = 1) -> str:
    offset = (max(page, 1) - 1) * PAGE_SIZE
    return f"{SEARCH_BASE}?q={quote(query)}&s={offset}"


def parse_search(html: str) -> tuple[list[dict], str | None]:
    items = _from_embedded_json(html)
    if items:
        return items, "g_page_config"

    items = _from_dom(html)
    if items:
        return items, "dom-item-link"

    return [], None


def looks_like_no_results(html: str) -> bool:
    sample = html[:30_000].lower()
    return any(m.lower() in sample for m in _NO_RESULT_MARKERS)


# ── Nhánh 1: object JS ───────────────────────────────────────────────────────


def _from_embedded_json(html: str) -> list[dict]:
    blob = extract_js_object(html, *_DATA_VARS)
    if blob is None:
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for key in _LIST_KEYS:
        for candidate in walk(blob, key):
            if not isinstance(candidate, list):
                continue
            for raw in candidate:
                if not isinstance(raw, dict):
                    continue
                parsed = _item_from_json(raw)
                if parsed and parsed["external_id"] not in seen:
                    seen.add(parsed["external_id"])
                    out.append(parsed)
        if out:
            break
    return out


def _item_from_json(raw: dict) -> dict | None:
    item_id = _first(raw, "nid", "item_id", "itemId", "id")
    if item_id is None:
        return None
    item_id = str(item_id).strip()
    if not item_id.isdigit():
        return None

    price = _first(raw, "view_price", "price", "reserve_price")
    price_minor = to_minor(price, CURRENCY) if price is not None else None

    pic = _first(raw, "pic_url", "picUrl", "img")
    if isinstance(pic, str) and pic.startswith("//"):
        pic = "https:" + pic

    return _row(
        external_id=item_id,
        title=_strip_tags(str(_first(raw, "raw_title", "title", "name") or "")),
        price_minor=price_minor,
        seller=_first(raw, "nick", "shopName", "seller_nick"),
        sales=_parse_sales(_first(raw, "view_sales", "sales", "biz30day")),
        images=[pic] if pic else None,
        url=_normalize_url(_first(raw, "detail_url", "url"), item_id),
        raw=raw,
    )


def _parse_sales(value: Any) -> int | None:
    """"123人付款" → 123 · "1.2万人付款" → 12000.

    万 = 10.000. Bỏ qua hậu tố này thì một sản phẩm bán 12.000 đơn bị ghi thành 1,
    tức là xếp hạng ngược hẳn — đúng thứ chỉ số S2.2 dùng để chấm điểm."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)

    m = _SALES_RE.search(str(value))
    if not m:
        digits = re.sub(r"[^\d]", "", str(value))
        return int(digits) if digits else None

    dec = parse_decimal(m.group(1))
    if dec is None:
        return None
    return int(dec * 10_000) if m.group(2) else int(dec)


# ── Nhánh 2: DOM dự phòng ────────────────────────────────────────────────────


def _from_dom(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    by_id: dict[str, dict] = {}

    for anchor in soup.select("a[href*='item.htm']"):
        m = _ITEM_ID_RE.search(anchor.get("href", ""))
        if not m:
            continue
        item_id = m.group(1)
        if item_id in by_id:
            continue

        card = anchor.parent
        text = card.get_text(" ", strip=True) if card else ""
        dec = parse_decimal(text)

        title = anchor.get("title") or anchor.get_text(" ", strip=True)
        if not title:
            continue

        by_id[item_id] = _row(
            external_id=item_id,
            title=title,
            price_minor=to_minor(dec, CURRENCY) if dec is not None else None,
            seller=None,
            sales=None,
            images=None,
            url=_normalize_url(anchor.get("href"), item_id),
            raw={"_parsed_by": "dom-item-link"},
        )

    return list(by_id.values())


# ── Dùng chung ───────────────────────────────────────────────────────────────


def _row(
    *,
    external_id: str,
    title: str,
    price_minor: int | None,
    seller: str | None,
    sales: int | None,
    images: list | None,
    url: str,
    raw: dict,
) -> dict:
    return {
        "source": "taobao",
        "shop_domain": "taobao.com",
        "external_id": external_id,
        "title": title,
        "url": url,
        "brand": None,
        "product_type": None,
        "currency": CURRENCY,
        "price_min_minor": price_minor,
        "price_max_minor": price_minor,
        "rating": None,
        "review_count": None,
        "sales_volume": sales,
        "available": None,
        "seller": str(seller) if seller else None,
        "is_sponsored": None,
        "image_refs": images,
        "raw": raw,
        "variants": (
            [
                {
                    "external_variant_id": external_id,
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


def _normalize_url(value: Any, item_id: str) -> str:
    if isinstance(value, str) and value:
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("http"):
            return value
    return f"https://item.taobao.com/item.htm?id={item_id}"


def _strip_tags(text: str) -> str:
    """`raw_title` có thể chứa thẻ <span class=H> bôi đậm từ khoá khớp."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _first(data: dict, *keys: str) -> Any:
    for k in keys:
        v = data.get(k)
        if v not in (None, "", []):
            return v
    return None
