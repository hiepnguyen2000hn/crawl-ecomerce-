"""Connector Bol.com — sàn thống trị thị trường NL/BE (thị trường SRS ưu tiên số 1).

Không có API công khai cho việc nghiên cứu đối thủ: Retailer API chỉ thấy đơn của
chính mình, và không vendor scraping nào phủ Bol.com. Nên đây là nguồn duy nhất mà
tự scrape vừa cần thiết vừa hợp lý — ngược hẳn với Amazon. Anti-bot nhẹ hơn nhiều.

CHIẾN LƯỢC PARSE — đọc kỹ trước khi sửa:

  1. Ưu tiên **JSON-LD** (`<script type="application/ld+json">`). Đây là dữ liệu có
     cấu trúc mà site tự công bố cho Google; nó **ổn định hơn CSS class nhiều lần**
     vì đổi nó là hỏng SEO. Lấy được giá / rating / review count từ đây là tốt nhất.

  2. Chỉ khi (1) không có mới rơi xuống **CSS selector**, và mọi selector gom hết vào
     `SELECTORS` bên dưới — sửa một chỗ, không phải đi lùng khắp file.

  3. Không đọc ra gì mà trang vẫn 200 và không có dấu hiệu chặn → `PARSE_FAIL`,
     **báo động chứ không retry**. Bol.com đổi layout là chuyện sẽ xảy ra;
     điều không được phép xảy ra là dữ liệu rỗng trôi âm thầm sang AI.

⚠️ Các selector ở `SELECTORS` là **điểm khởi đầu, cần kiểm chứng bằng HTML thật**
   trong lần chạy đầu tiên. Dùng `GET /api/v1/ecom/bol/probe` để xem parser đọc
   được gì trước khi chạy job hàng loạt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from app.crawl import http
from app.crawl.normalize import parse_decimal, to_minor
from app.crawl.outcomes import Outcome

BASE = "https://www.bol.com"
SEARCH_PATH = "/nl/nl/s/"
DEFAULT_CURRENCY = "EUR"

#: `/nl/nl/p/<slug>/<id>/` — id là phần số dài ở cuối, đây là khoá tự nhiên.
#: Neo parser vào ĐƯỜNG DẪN chứ không vào class CSS: Bol.com đã chuyển sang Tailwind
#: (`row-span-2`, `col-start-2`, `font-bold`...) nên class không còn mang ngữ nghĩa và
#: đổi bất cứ lúc nào. Cấu trúc URL thì không đổi được — đổi là hỏng mọi link và SEO.
_PRODUCT_ID_RE = re.compile(r"/p/[^/]*/(\d{6,})")

#: Bol.com nhúng giá dưới dạng text trợ năng cho screen-reader:
#:     "De prijs van dit product is '89' euro en '99' cent"
#: Đây là đường trích giá BỀN NHẤT trên trang này — ổn định hơn hẳn việc ghép hai
#: <span> Tailwind rời nhau, và không vỡ khi họ đổi layout.
_PRICE_A11Y_RE = re.compile(
    r"prijs van dit product is\s*['‘’\"]?(\d+)['‘’\"]?\s*euro"
    r"(?:\s*en\s*['‘’\"]?(\d+)['‘’\"]?\s*cent)?",
    re.IGNORECASE,
)

#: "Gemiddeld 4.6 van de 5 sterren uit 194 reviews" — cũng là nhãn trợ năng.
_RATING_A11Y_RE = re.compile(
    r"Gemiddeld\s+([\d.,]+)\s+van de\s+\d+\s+sterren\s+uit\s+([\d.]+)\s+review",
    re.IGNORECASE,
)

#: Số tầng tối đa leo lên từ thẻ <a> để tìm ranh giới một thẻ sản phẩm.
_MAX_CARD_DEPTH = 7

#: "Verkoop door <tên shop>" — người bán. Trên marketplace đây mới là đối thủ thật,
#: không phải "bol.com". SRS Bước 3 cần biết ai đang bán để so giá.
_SELLER_RE = re.compile(r"^\s*Verkoop door\s+(.{2,60}?)\s*$", re.IGNORECASE)

#: Tình trạng hàng, theo cách Bol.com diễn đạt bằng tiếng Hà Lan.
_IN_STOCK = ("op voorraad", "morgen in huis", "vandaag besteld")
_OUT_OF_STOCK = ("niet leverbaar", "uitverkocht", "niet op voorraad", "tijdelijk niet")


@dataclass
class BolSearchResult:
    outcome: Outcome
    query: str
    products: list[dict] = field(default_factory=list)
    pages_fetched: int = 0
    latency_ms: int = 0
    error: str | None = None
    parse_source: str | None = None
    """'json-ld' hoặc 'css' — biết parser đang chạy nhánh nào là thông tin vận hành
    quan trọng: khi nhánh json-ld ngừng hoạt động, ta biết trước khi dữ liệu hỏng."""
    raw_sample: str = ""


async def search(
    query: str,
    *,
    max_pages: int = 3,
    country_path: str = "/nl/nl",
) -> BolSearchResult:
    """Tìm sản phẩm theo từ khoá trên Bol.com.

    `country_path` để sẵn cho `/be/nl` (Bỉ) — cùng catalog, khác giá và khác giao hàng.
    """
    result = BolSearchResult(outcome=Outcome.OK, query=query)
    collected: list[dict] = []

    for page in range(1, max_pages + 1):
        resp = await http.fetch(
            f"{BASE}{country_path}/s/",
            params={"searchtext": query, "page": page},
            # Ngôn ngữ phải khớp geo của request — nl cho thị trường Hà Lan.
            headers={"Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8"},
        )
        result.latency_ms += resp.latency_ms

        if resp.outcome is not None:
            result.outcome = resp.outcome
            result.error = resp.error or f"HTTP {resp.status_code} tại trang {page}"
            break

        if page == 1:
            result.raw_sample = resp.text[:2000]

        items, parse_source = _parse_listing(resp.text)
        result.parse_source = parse_source

        if not items:
            if page == 1:
                # Trang đầu mà không đọc ra gì: hoặc thật sự không có kết quả,
                # hoặc layout đã đổi. Phân biệt bằng dấu hiệu "không có kết quả".
                result.outcome = (
                    Outcome.EMPTY if _looks_like_no_results(resp.text) else Outcome.PARSE_FAIL
                )
                result.error = (
                    None
                    if result.outcome is Outcome.EMPTY
                    else "200 nhưng không đọc ra sản phẩm nào — nhiều khả năng Bol.com đã đổi layout"
                )
            break

        collected.extend(items)
        result.pages_fetched = page

    if collected:
        result.products = collected
        result.outcome = Outcome.OK
    return result


# ── Parsing ──────────────────────────────────────────────────────────────────


def _parse_listing(html: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(html, "lxml")

    items = _from_json_ld(soup)
    if items:
        return items, "json-ld"

    items = _from_css(soup)
    if items:
        return items, "search-listing"

    return [], None


def _from_json_ld(soup: BeautifulSoup) -> list[dict]:
    """Đọc ItemList / Product từ JSON-LD. Đường ưu tiên."""
    out: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for node in _iter_nodes(data):
            if node.get("@type") == "Product":
                parsed = _product_from_ld(node)
                if parsed:
                    out.append(parsed)
            elif node.get("@type") == "ItemList":
                for el in node.get("itemListElement") or []:
                    item = el.get("item") if isinstance(el, dict) else None
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        parsed = _product_from_ld(item)
                        if parsed:
                            out.append(parsed)
    return out


def _iter_nodes(data: Any):
    """JSON-LD có thể là object, list, hoặc có @graph — duyệt phẳng hết."""
    if isinstance(data, list):
        for d in data:
            yield from _iter_nodes(d)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "itemListElement", "mainEntity"):
            if key in data:
                yield from _iter_nodes(data[key])


def _product_from_ld(node: dict) -> dict | None:
    url = node.get("url") or ""
    external_id = _extract_id(url) or str(node.get("sku") or node.get("productID") or "").strip()
    if not external_id:
        return None

    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    currency = offers.get("priceCurrency") or DEFAULT_CURRENCY
    price_minor = to_minor(str(offers.get("price") or ""), currency)

    agg = node.get("aggregateRating") or {}
    rating = parse_decimal(str(agg.get("ratingValue"))) if agg.get("ratingValue") else None
    try:
        review_count = int(agg.get("reviewCount") or agg.get("ratingCount") or 0) or None
    except (TypeError, ValueError):
        review_count = None

    image = node.get("image")
    images = [image] if isinstance(image, str) else (image if isinstance(image, list) else None)

    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")

    return {
        "source": "bol",
        "shop_domain": "bol.com",
        "external_id": external_id,
        "title": node.get("name") or "",
        "url": url if url.startswith("http") else f"{BASE}{url}",
        "brand": brand,
        "product_type": None,
        "currency": currency,
        "price_min_minor": price_minor,
        "price_max_minor": price_minor,
        "rating": float(rating) if rating is not None else None,
        "review_count": review_count,
        "sales_volume": None,  # Bol.com không công bố — xem docstring model EcomProduct
        "available": _availability(offers.get("availability")),
        "seller": (node.get("offers") or {}).get("seller", {}).get("name")
        if isinstance(node.get("offers"), dict)
        else None,
        "is_sponsored": None,
        "image_refs": images,
        "raw": node,
        "variants": (
            [
                {
                    "external_variant_id": external_id,
                    "variant_title": None,
                    "sku": node.get("sku"),
                    "price_minor": price_minor,
                    "compare_at_minor": None,
                    "available": _availability(offers.get("availability")),
                }
            ]
            if price_minor is not None
            else []
        ),
    }


def _from_css(soup: BeautifulSoup) -> list[dict]:
    """Nhánh dùng cho trang kết quả tìm kiếm (trang này không có JSON-LD).

    Cách làm: gom mọi thẻ <a> trỏ tới `/p/<slug>/<id>/`, gộp theo id, rồi leo ngược
    lên DOM tới khi gặp tổ tiên vừa chứa giá vừa **chỉ chứa đúng một sản phẩm** —
    đó là ranh giới thẻ sản phẩm. Không phụ thuộc class nào.
    """
    by_id: dict[str, dict] = {}

    for anchor in soup.select("a[href]"):
        external_id = _extract_id(anchor.get("href", ""))
        if not external_id or external_id in by_id:
            continue

        card = _find_card(anchor, external_id)
        if card is None:
            continue

        parsed = _product_from_card(card, external_id, anchor.get("href", ""))
        if parsed:
            by_id[external_id] = parsed

    return list(by_id.values())


def _find_card(anchor, external_id: str):
    """Leo lên tìm tổ tiên nhỏ nhất chứa giá và không lẫn sang sản phẩm khác."""
    node = anchor
    for _ in range(_MAX_CARD_DEPTH):
        node = node.parent
        if node is None or node.name in ("body", "html"):
            return None
        text = node.get_text(" ", strip=True)
        if not _PRICE_A11Y_RE.search(text):
            continue
        ids = {
            _extract_id(a.get("href", ""))
            for a in node.select("a[href]")
            if _extract_id(a.get("href", ""))
        }
        if ids == {external_id}:
            return node
    return None


def _product_from_card(card, external_id: str, href: str) -> dict | None:
    text = card.get_text(" ", strip=True)

    m = _PRICE_A11Y_RE.search(text)
    price_minor = (int(m.group(1)) * 100 + int(m.group(2) or 0)) if m else None

    rating = review_count = None
    for el in card.select("[aria-label]"):
        rm = _RATING_A11Y_RE.search(el.get("aria-label") or "")
        if rm:
            rating = float(parse_decimal(rm.group(1)) or 0) or None
            review_count = int(rm.group(2).replace(".", "")) or None
            break

    # Thẻ <a> bọc ảnh thường rỗng text; thẻ mang tiêu đề mới có chữ.
    title = ""
    for a in card.select("a[href]"):
        if _extract_id(a.get("href", "")) == external_id:
            t = a.get_text(" ", strip=True)
            if len(t) > len(title):
                title = t

    img = card.select_one("img[src]")
    image = img.get("src") if img else None

    # Người bán: lấy từ node text cụ thể chứ không regex trên text phẳng — nhãn
    # "Verkoop door" xuất hiện hai lần (label + giá trị) nên cắt trên text phẳng dễ sai.
    seller = None
    for s in card.stripped_strings:
        m = _SELLER_RE.match(s)
        if m:
            seller = m.group(1).strip()
            break

    low = text.lower()
    if any(k in low for k in _OUT_OF_STOCK):
        available = False
    elif any(k in low for k in _IN_STOCK):
        available = True
    else:
        available = None

    if not title and price_minor is None:
        return None

    return {
        "source": "bol",
        "shop_domain": "bol.com",
        "external_id": external_id,
        "title": title,
        "url": href if href.startswith("http") else f"{BASE}{href}",
        "brand": None,
        "product_type": None,
        "currency": DEFAULT_CURRENCY,
        "price_min_minor": price_minor,
        "price_max_minor": price_minor,
        "rating": rating,
        "review_count": review_count,
        "sales_volume": None,  # Bol.com không công bố — xem docstring EcomProduct
        "available": available,
        "seller": seller,
        "is_sponsored": "Gesponsord" in text,
        "image_refs": [image] if image else None,
        "raw": {"_parsed_by": "search-listing"},
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


def _extract_id(url: str) -> str | None:
    m = _PRODUCT_ID_RE.search(url or "")
    return m.group(1) if m else None


def _availability(value: Any) -> bool | None:
    if not isinstance(value, str):
        return None
    return "InStock" in value


def _looks_like_no_results(html: str) -> bool:
    """Phân biệt 'không có kết quả' (EMPTY) với 'đổi layout' (PARSE_FAIL).

    Nhầm hai cái này rất tốn: một bên là câu trả lời hợp lệ, một bên là bug im lặng.
    """
    sample = html[:20000].lower()
    return any(
        k in sample
        for k in (
            "geen resultaten",
            "niets gevonden",
            "no results",
            "0 resultaten",
            "helaas geen",
        )
    )
