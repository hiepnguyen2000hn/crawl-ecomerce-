"""Đọc trang tìm kiếm 1688 → hình dạng chuẩn của `ecom_products`.

Thuần hàm, không I/O.

**Vì sao dùng chung bảng `ecom_products` chứ không dựng bảng nhà cung cấp riêng:**
1688 phục vụ SRS Bước 4 (tìm nguồn hàng), khác vai trò với Amazon/Bol.com (Bước 2, tìm
sản phẩm đang thắng). Nhưng hình dạng dữ liệu thì trùng khít — tên, giá, ảnh, người
bán, link — và `seller` vốn đã được định nghĩa là "ai đang bán mẫu này". Với 1688,
người bán chính là nhà cung cấp.

Để chung bảng còn được thêm một thứ quan trọng: giá vốn (1688) và giá bán của đối thủ
(Amazon/Bol) **nằm cạnh nhau, so được bằng một câu query** — đúng phép so `COGS_max`
mà Bước 3 cần. Tách bảng là tự tạo ra một phép join không ai được lợi.

Thứ 1688 có mà bảng này chưa có cột: MOQ (số lượng đặt tối thiểu) và bảng giá bậc
thang. Hai thứ đó đang nằm trong `raw`. Khi nào có người thật sự cần **lọc/sắp theo**
chúng thì hãy nâng lên thành cột — thêm cột mà không ai query chỉ làm schema nặng thêm.

CHIẾN LƯỢC PARSE:
  1. **Ưu tiên object JSON nhúng trong JS.** 1688 render danh sách bằng JS; HTML trả
     về gần như trống nên đọc DOM là con đường sai ngay từ đầu.
  2. Dự phòng: gom link `detail.1688.com/offer/<id>.html` trong DOM. Neo vào **cấu
     trúc URL** chứ không vào class — URL không đổi được vì đổi là hỏng mọi link cũ.
  3. Không ra gì mà trang vẫn 200 và không có dấu hiệu chặn → `PARSE_FAIL`.

⚠️ Tên biến JS ở `_DATA_VARS` là **điểm khởi đầu, cần kiểm chứng bằng HTML thật**.
Dùng `GET /api/v1/ecom/1688/probe` để xem parser đọc được gì trước khi chạy job hàng
loạt — đúng quy trình đã áp cho Bol.com.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from app.crawl.html_json import extract_js_object, walk
from app.crawl.normalize import parse_decimal, to_minor

CURRENCY = "CNY"
SEARCH_BASE = "https://s.1688.com/selloffer/offer_search.htm"

#: Các tên biến toàn cục từng thấy mang dữ liệu danh sách. Thử lần lượt.
_DATA_VARS = (
    "window.__INIT_DATA__",
    "__INIT_DATA__",
    "window.__NUXT__",
    "window.data",
    "window.__AWP_DATA__",
)

#: Khoá chứa mảng offer bên trong object JSON. Tìm theo tên khoá ở mọi độ sâu thay vì
#: bám đường dẫn tuyệt đối — họ bọc thêm một lớp là đường dẫn tuyệt đối vỡ ngay.
_LIST_KEYS = ("offerList", "offers", "data", "list", "items")

#: `detail.1688.com/offer/123456789.html` — id là phần số, đây là khoá tự nhiên.
_OFFER_ID_RE = re.compile(r"detail\.1688\.com/offer/(\d{6,})")

_NO_RESULT_MARKERS = ("没有找到", "无相关", "没有找到相关", "no results")


def search_url(query: str, page: int = 1) -> str:
    return f"{SEARCH_BASE}?keywords={quote(query)}&beginPage={page}"


def parse_search(html: str) -> tuple[list[dict], str | None]:
    """HTML trang tìm kiếm → (danh sách offer chuẩn hoá, tên nhánh parser)."""
    items = _from_embedded_json(html)
    if items:
        return items, "embedded-json"

    items = _from_dom(html)
    if items:
        return items, "dom-offer-link"

    return [], None


def looks_like_no_results(html: str) -> bool:
    sample = html[:30_000].lower()
    return any(m.lower() in sample for m in _NO_RESULT_MARKERS)


# ── Nhánh 1: object JSON nhúng ───────────────────────────────────────────────


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
                parsed = _offer_from_json(raw)
                if parsed and parsed["external_id"] not in seen:
                    seen.add(parsed["external_id"])
                    out.append(parsed)
        if out:
            break
    return out


def _offer_from_json(raw: dict) -> dict | None:
    offer_id = _first(raw, "offerId", "offerid", "id", "productId")
    if offer_id is None:
        return None
    offer_id = str(offer_id).strip()
    if not offer_id.isdigit():
        return None

    price_min, price_max = _price_range(raw)

    company = _first(raw, "companyName", "company", "sellerNick", "supplierName", "memberName")
    if isinstance(company, dict):
        company = _first(company, "name", "companyName")

    image = _first(raw, "imgUrl", "image", "picUrl", "mainImage")
    if isinstance(image, dict):
        image = _first(image, "imgUrl", "url")

    return _row(
        external_id=offer_id,
        title=str(_first(raw, "subject", "title", "name") or ""),
        price_min=price_min,
        price_max=price_max,
        seller=str(company) if company else None,
        sales=_int_or_none(_first(raw, "saleQuantity", "sold", "tradeQuantity", "saleCount")),
        images=[str(image)] if image else None,
        raw=raw,
    )


def _price_range(raw: dict) -> tuple[int | None, int | None]:
    """1688 báo giá theo **bậc số lượng**, nên một offer thường có dải giá, không phải
    một con số. Giữ cả hai đầu: đầu thấp là giá khi đặt nhiều — đó mới là giá vốn thật
    khi lên đơn, còn đầu cao là giá mẫu thử."""
    info = raw.get("priceInfo")
    if isinstance(info, dict):
        low = _first(info, "price", "minPrice", "low")
        high = _first(info, "maxPrice", "high") or low
    else:
        low = _first(raw, "price", "minPrice", "unitPrice", "showPrice")
        high = _first(raw, "maxPrice") or low

    # Dạng chuỗi dải "1.50-3.00" vẫn gặp ở nhánh DOM lẫn một số phiên bản JSON.
    if isinstance(low, str) and "-" in low:
        parts = [p for p in low.split("-") if p.strip()]
        if len(parts) == 2:
            low, high = parts[0], parts[1]

    return to_minor(low, CURRENCY) if low is not None else None, (
        to_minor(high, CURRENCY) if high is not None else None
    )


# ── Nhánh 2: DOM dự phòng ────────────────────────────────────────────────────


def _from_dom(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    by_id: dict[str, dict] = {}

    for anchor in soup.select("a[href]"):
        m = _OFFER_ID_RE.search(anchor.get("href", ""))
        if not m:
            continue
        offer_id = m.group(1)
        if offer_id in by_id:
            continue

        card = _find_card(anchor, offer_id)
        if card is None:
            continue

        text = card.get_text(" ", strip=True)
        dec = parse_decimal(text)
        price_minor = to_minor(dec, CURRENCY) if dec is not None else None

        title = anchor.get("title") or anchor.get_text(" ", strip=True)
        img = card.select_one("img[src]") or card.select_one("img[data-src]")
        image = (img.get("src") or img.get("data-src")) if img else None

        if not title and price_minor is None:
            continue

        by_id[offer_id] = _row(
            external_id=offer_id,
            title=title or "",
            price_min=price_minor,
            price_max=price_minor,
            seller=None,
            sales=None,
            images=[image] if image else None,
            raw={"_parsed_by": "dom-offer-link"},
        )

    return list(by_id.values())


def _find_card(anchor: Any, offer_id: str, max_depth: int = 6) -> Any | None:
    """Leo lên tìm tổ tiên nhỏ nhất chỉ chứa đúng một offer — ranh giới thẻ sản phẩm."""
    node = anchor
    for _ in range(max_depth):
        node = node.parent
        if node is None or node.name in ("body", "html"):
            return None
        ids = {
            m.group(1)
            for a in node.select("a[href]")
            if (m := _OFFER_ID_RE.search(a.get("href", "")))
        }
        if ids == {offer_id}:
            return node
    return None


# ── Dùng chung ───────────────────────────────────────────────────────────────


def _row(
    *,
    external_id: str,
    title: str,
    price_min: int | None,
    price_max: int | None,
    seller: str | None,
    sales: int | None,
    images: list | None,
    raw: dict,
) -> dict:
    return {
        "source": "alibaba_1688",
        "shop_domain": "1688.com",
        "external_id": external_id,
        "title": title,
        "url": f"https://detail.1688.com/offer/{external_id}.html",
        "brand": None,
        "product_type": None,
        "currency": CURRENCY,
        "price_min_minor": price_min,
        "price_max_minor": price_max or price_min,
        # 1688 có hiển thị đánh giá nhà cung cấp nhưng không phải đánh giá sản phẩm —
        # để None còn hơn nhét nhầm điểm của shop vào cột rating của sản phẩm.
        "rating": None,
        "review_count": None,
        # Khác Amazon/Bol: 1688 CÓ công bố số đã bán (成交量). Đây là nguồn hiếm hoi
        # điền được trường này — xem docs §2.2.
        "sales_volume": sales,
        "available": None,
        "seller": seller,
        "is_sponsored": None,
        "image_refs": images,
        "raw": raw,
        "variants": (
            [
                {
                    "external_variant_id": external_id,
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


def _first(data: dict, *keys: str) -> Any:
    for k in keys:
        v = data.get(k)
        if v not in (None, "", []):
            return v
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
