"""Đọc trang kết quả tìm kiếm Amazon → hình dạng chuẩn của `ecom_products`.

Thuần hàm, không I/O — đưa vào HTML, nhận ra list dict. Tách khỏi adapter để test
được bằng HTML lưu sẵn, không cần mở trình duyệt.

CHIẾN LƯỢC PARSE — đọc kỹ trước khi sửa:

  1. **Neo vào `data-asin`, không neo vào class.** ASIN là mã sản phẩm của chính
     Amazon, JS của họ đọc nó để dựng giỏ hàng — đổi là hỏng site của họ. Class CSS
     (`s-result-item`, `a-section`...) thì đổi theo từng đợt A/B test.

  2. **Giá lấy từ `.a-price .a-offscreen`.** Đây là text dành cho trình đọc màn hình
     ("$29.99"), trong khi giá hiển thị bị tách thành nhiều `<span>` rời (ký hiệu /
     phần nguyên / phần lẻ). Ghép mấy span đó lại vừa dễ sai vừa vỡ mỗi lần họ đổi
     layout; bản trợ năng ổn định hơn hẳn và luôn là một chuỗi hoàn chỉnh.

  3. **Rating lấy từ `.a-icon-alt`** — "4.6 out of 5 stars", cũng là text trợ năng.

  4. Không đọc ra gì mà trang vẫn 200 và không có dấu hiệu chặn → `PARSE_FAIL`,
     **báo động chứ không retry**.

⚠️ Amazon KHÔNG công bố số đã bán (`sales_volume` luôn None). Chỉ số thay thế là
`bestseller_rank` + `review_count` + tốc độ tăng review — xem docs §2.2.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from app.crawl.normalize import to_minor

#: Marketplace → (mã tiền tệ, Accept-Language). Ngôn ngữ phải khớp geo, lệch là cờ đỏ.
#:
#: SRS nhắm DE · FR · IT · ES (xem docs §2.1 — NL/BE là sân của Bol.com, không phải
#: Amazon). `.com` và `.co.uk` để sẵn cho việc đối chiếu, không phải thị trường target.
MARKETPLACES: dict[str, tuple[str, str]] = {
    "amazon.de": ("EUR", "de-DE,de;q=0.9"),
    "amazon.fr": ("EUR", "fr-FR,fr;q=0.9"),
    "amazon.it": ("EUR", "it-IT,it;q=0.9"),
    "amazon.es": ("EUR", "es-ES,es;q=0.9"),
    "amazon.nl": ("EUR", "nl-NL,nl;q=0.9"),
    "amazon.co.uk": ("GBP", "en-GB,en;q=0.9"),
    "amazon.com": ("USD", "en-US,en;q=0.9"),
}

DEFAULT_MARKETPLACE = "amazon.de"

#: "4.6 out of 5 stars" · "4,6 von 5 Sternen" · "4,6 sur 5 étoiles" — số đứng đầu là
#: rating ở mọi ngôn ngữ, nên chỉ cần bắt số đầu tiên của chuỗi `.a-icon-alt`.
_RATING_RE = re.compile(r"^\s*([\d.,]+)\s")

#: Số review nằm trong `aria-label` dạng "1,234 ratings" hoặc là text trần của thẻ <a>
#: trỏ tới phần đánh giá. Bỏ mọi dấu ngăn nghìn rồi mới ép kiểu.
_DIGITS_RE = re.compile(r"[\d.,\s]+")

#: Cụm "không có kết quả" theo từng ngôn ngữ marketplace. Dùng để phân biệt EMPTY
#: (câu trả lời hợp lệ) với PARSE_FAIL (layout đã đổi) — nhầm hai cái này rất tốn.
_NO_RESULT_MARKERS = (
    "no results for",
    "did not match any products",
    "keine ergebnisse für",
    "aucun résultat pour",
    "nessun risultato per",
    "no hay resultados para",
    "geen resultaten voor",
)


def search_url(query: str, marketplace: str, page: int = 1) -> str:
    return f"https://www.{marketplace}/s?k={query}&page={page}"


def currency_for(marketplace: str) -> str:
    return MARKETPLACES.get(marketplace, ("EUR", ""))[0]


def accept_language_for(marketplace: str) -> str:
    return MARKETPLACES.get(marketplace, ("", "en-US,en;q=0.9"))[1]


def parse_search(html: str, marketplace: str) -> tuple[list[dict], str | None]:
    """HTML trang `/s?k=...` → (danh sách sản phẩm chuẩn hoá, tên nhánh parser).

    Nhánh parser trả ra để ghi vào kết quả job: biết parser đang đi đường nào là
    thông tin vận hành, khi nó đổi ta biết TRƯỚC lúc dữ liệu bắt đầu thiếu trường.
    """
    soup = BeautifulSoup(html, "lxml")
    currency = currency_for(marketplace)

    cards = [
        c
        for c in soup.select("div[data-asin]")
        if (c.get("data-asin") or "").strip()
    ]
    if not cards:
        return [], None

    out: list[dict] = []
    seen: set[str] = set()
    for card in cards:
        asin = (card.get("data-asin") or "").strip()
        if asin in seen:
            continue
        parsed = _product_from_card(card, asin, marketplace, currency)
        if parsed:
            seen.add(asin)
            out.append(parsed)

    return out, "data-asin" if out else None


def looks_like_no_results(html: str) -> bool:
    sample = html[:30_000].lower()
    return any(m in sample for m in _NO_RESULT_MARKERS)


# ── Nội bộ ───────────────────────────────────────────────────────────────────


def _product_from_card(card: Any, asin: str, marketplace: str, currency: str) -> dict | None:
    title = _text(card.select_one("h2 span")) or _text(card.select_one("h2"))

    price_minor = _price_minor(card, currency)
    # Thẻ không có cả tiêu đề lẫn giá thì không phải thẻ sản phẩm — Amazon chèn nhiều
    # `div[data-asin=""]` làm khung quảng cáo/gợi ý giữa danh sách.
    if not title and price_minor is None:
        return None

    rating, review_count = _rating_and_reviews(card)

    img = card.select_one("img.s-image") or card.select_one("img[src]")
    image = img.get("src") if img else None

    return {
        "source": "amazon",
        # Mỗi marketplace một dòng riêng: cùng ASIN nhưng giá/tồn kho khác nhau giữa
        # amazon.de và amazon.fr, gộp lại là mất đúng thứ Bước 3 cần để so giá.
        "shop_domain": marketplace,
        "external_id": asin,
        "title": title or "",
        "url": f"https://www.{marketplace}/dp/{asin}",
        "brand": None,
        "product_type": None,
        "currency": currency,
        "price_min_minor": price_minor,
        "price_max_minor": price_minor,
        "rating": rating,
        "review_count": review_count,
        # Amazon không công bố số đã bán — xem docstring đầu file và docs §2.2.
        "sales_volume": None,
        "available": None if price_minor is None else True,
        # Trên trang kết quả chỉ biết "ai bán" khi là offer của bên thứ ba; trang
        # search không hiện, phải vào trang chi tiết. Để None còn hơn điền bừa.
        "seller": None,
        "is_sponsored": _is_sponsored(card),
        "image_refs": [image] if image else None,
        "raw": {"_parsed_by": "data-asin", "asin": asin},
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


def _price_minor(card: Any, currency: str) -> int | None:
    el = card.select_one(".a-price .a-offscreen")
    if el is None:
        return None
    return to_minor(_text(el), currency)


def _rating_and_reviews(card: Any) -> tuple[float | None, int | None]:
    rating = None
    alt = _text(card.select_one(".a-icon-alt"))
    if alt:
        m = _RATING_RE.match(alt)
        if m:
            rating = _rating_value(m.group(1))

    review_count = None
    for el in card.select("a[href*='customerReviews'] span, span[aria-label]"):
        raw = el.get("aria-label") or el.get_text(strip=True)
        # Nhãn rating ("4,6 out of 5" / "4,6 von 5 Sternen") cũng toàn chữ số nên sẽ
        # bị nhận nhầm thành số review. Loại theo TỪ chỉ sao của từng ngôn ngữ chứ
        # không chỉ theo "out of" — marketplace target là DE/FR/IT/ES, không phải EN.
        if not raw or _mentions_stars(raw):
            continue
        digits = re.sub(r"[^\d]", "", raw)
        if digits and len(digits) <= 9:
            review_count = int(digits)
            break

    return rating, review_count


def _rating_value(text: str) -> float | None:
    """"4.6" hoặc "4,6" → 4.6.

    KHÔNG dùng `parse_decimal` ở đây: hàm đó dành cho tiền và giả định 2 chữ số lẻ,
    nên "4,6" bị nó hiểu là dấu ngăn nghìn và trả về 46 — rating vọt lên gấp 10 lần.
    Rating thì luôn chỉ có một chữ số lẻ, đổi dấu phẩy thành dấu chấm là xong.
    """
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None


#: Từ chỉ "sao" theo ngôn ngữ marketplace — dùng để loại nhãn rating khi tìm số review.
_STAR_WORDS = ("out of", "sterne", "sternen", "étoile", "estrella", "stelle", "sterren")


def _mentions_stars(text: str) -> bool:
    low = text.lower()
    return "/" in low or any(w in low for w in _STAR_WORDS)


def _is_sponsored(card: Any) -> bool | None:
    text = card.get_text(" ", strip=True).lower()
    return any(k in text for k in ("sponsored", "gesponsert", "sponsorisé", "patrocinado"))


def _text(node: Any) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""
