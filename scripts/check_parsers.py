"""Kiểm tra parser Amazon · 1688 · Taobao bằng HTML mẫu — không cần mạng, DB hay trình duyệt.

    python scripts/check_parsers.py

Chạy script này **sau mỗi lần sửa selector/tên biến JS**. Ba nguồn này đọc cấu trúc
trang của sàn, mà sàn thì đổi layout không báo trước — khi đó việc phải làm là sửa
`app/sources/<nguồn>/normalize.py` rồi chạy lại đây để chắc chưa làm hỏng phần khác.

Hai nhóm kiểm tra:
  1. **Parser** — HTML mẫu → đúng hình dạng `ecom_products`. Bao gồm mấy chỗ đã từng
     sai: rating "4,6" kiểu Đức (dễ thành 46), "1.2万人付款" của Taobao (dễ thành 1),
     và dải giá bậc thang của 1688.
  2. **Adapter + vòng lặp phân trang** — giả lập tầng trình duyệt để kiểm phần logic
     khó nhất: khi nào EMPTY, khi nào PARSE_FAIL, và có giữ lại dữ liệu khi gãy giữa
     chừng không.

Đây KHÔNG thay thế `/probe`: script chạy trên HTML mẫu, còn `/probe` chạy trên HTML
thật. Sửa selector thì dùng `/probe` để xem sàn đang trả về gì, rồi dùng script này
để chốt là parser vẫn đúng ở mọi trường hợp đã biết.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawl import browser_fetch
from app.crawl.contracts import Capability, FetchRequest
from app.crawl.outcomes import Outcome, looks_blocked
from app.sources.alibaba_1688 import normalize as ali
from app.sources.amazon import normalize as amz
from app.sources.amazon.browser import AmazonBrowserAdapter
from app.sources.amazon.vendor import AmazonVendorAdapter
from app.sources.taobao import normalize as tb
from app.sources.taobao.browser import TaobaoBrowserAdapter
from app.sources.taobao.vendor import TaobaoVendorAdapter

_fails: list[str] = []


def check(desc: str, got, want) -> None:
    ok = got == want
    if not ok:
        _fails.append(f"{desc}: got={got!r} want={want!r}")
    print(("  PASS " if ok else "  FAIL ") + desc + ("" if ok else f"   got={got!r} want={want!r}"))


# ══ HTML mẫu ════════════════════════════════════════════════════════════════

AMAZON_HTML = """
<div class="s-main-slot">
  <div data-component-type="s-search-result" data-asin="B08N5WRWNW">
    <h2><a href="/dp/B08N5WRWNW"><span>Echo Dot (4. Generation) Smart Speaker</span></a></h2>
    <span class="a-price"><span class="a-offscreen">39,99&nbsp;&euro;</span></span>
    <i class="a-icon-star-small"><span class="a-icon-alt">4,6 von 5 Sternen</span></i>
    <span aria-label="12.345 Bewertungen">12.345</span>
    <img class="s-image" src="https://m.media-amazon.com/images/I/echo.jpg">
  </div>
  <div data-component-type="s-search-result" data-asin="B07PGL2ZSL">
    <h2><a href="/dp/B07PGL2ZSL"><span>Gesponsert Mini Ventilator</span></a></h2>
    <span class="a-price"><span class="a-offscreen">1.299,00&nbsp;&euro;</span></span>
    <i><span class="a-icon-alt">4,1 von 5 Sternen</span></i>
  </div>
  <div data-asin=""><span>khung quảng cáo, không phải sản phẩm</span></div>
</div>
"""

ALI_JSON_HTML = """
<html><body><script>
window.__INIT_DATA__ = {"data":{"offerList":[
 {"offerId":641234567890,"subject":"便携式榨汁机 USB充电","priceInfo":{"price":"12.50","maxPrice":"18.00"},
  "companyName":"义乌市小家电有限公司","imgUrl":"https://cbu01.alicdn.com/a.jpg","saleQuantity":"3200"},
 {"offerId":"641234567891","subject":"迷你风扇","price":"5.80",
  "company":"深圳风扇厂","imgUrl":"https://cbu01.alicdn.com/b.jpg","sold":120}
]}};
</script></body></html>
"""

ALI_DOM_HTML = """
<html><body>
 <div><a href="https://detail.1688.com/offer/777888999000.html" title="保温杯不锈钢">保温杯</a>
   <span>25.90</span><img src="https://cbu01.alicdn.com/c.jpg"></div>
</body></html>
"""

#: Một item THẬT trong dataset của actor `junglee/Amazon-crawler`, chép từ README của
#: chính actor. Giữ nguyên hình dạng gốc — đây là hợp đồng mà `_map_item` phải đọc được.
#: Chú ý `price` là object lồng và `price.currency` là KÝ HIỆU chứ không phải mã ISO.
AMAZON_ACTOR_ITEM = {
    "title": "SanDisk 1TB Extreme microSDXC UHS-I Memory Card with Adapter",
    "url": "https://www.amazon.com/dp/B09X7MPX8L",
    "asin": "B09X7MPX8L",
    "inStock": True,
    "brand": "SanDisk",
    "price": {"value": 145.5, "currency": "$"},
    "listPrice": {"value": 299.99, "currency": "$"},
    "stars": 4.8,
    "reviewsCount": 36704,
    "breadCrumbs": "Electronics › Computer Accessories › Memory Cards",
    "thumbnailImage": "https://m.media-amazon.com/images/I/716kSUlHouL.jpg",
}

TAOBAO_HTML = """
<html><body><script>
g_page_config = {"mods":{"itemlist":{"data":{"auctions":[
 {"nid":"612345678901","raw_title":"便携<span class=H>榨汁机</span>","view_price":"29.90",
  "nick":"小家电旗舰店","view_sales":"1.2万人付款","pic_url":"//img.alicdn.com/a.jpg"},
 {"nid":"612345678902","raw_title":"迷你风扇","view_price":"15.00",
  "nick":"风扇专营店","view_sales":"338人付款","pic_url":"//img.alicdn.com/b.jpg"}
]}}}};
</script></body></html>
"""


# ══ 1. Parser ═══════════════════════════════════════════════════════════════


def check_parsers() -> None:
    print("\n=== AMAZON (amazon.de — số kiểu Đức) ===")
    items, psrc = amz.parse_search(AMAZON_HTML, "amazon.de")
    check("đọc 2 sản phẩm, bỏ khung data-asin rỗng", len(items), 2)
    check("nhánh parser", psrc, "data-asin")
    p = items[0]
    check("external_id = ASIN", p["external_id"], "B08N5WRWNW")
    check("giá 39,99 EUR → 3999 minor", p["price_min_minor"], 3999)
    check("rating 4,6 → 4.6 (KHÔNG phải 46)", p["rating"], 4.6)
    check("số review 12.345 → 12345", p["review_count"], 12345)
    check("shop_domain = marketplace (giá mỗi nước một khác)", p["shop_domain"], "amazon.de")
    check("sales_volume None — Amazon không công bố", p["sales_volume"], None)
    check("giá nghìn 1.299,00 EUR → 129900", items[1]["price_min_minor"], 129900)
    check("nhận diện quảng cáo (Gesponsert)", items[1]["is_sponsored"], True)
    check("phát hiện trang không kết quả (DE)", amz.looks_like_no_results("Keine Ergebnisse für x"), True)
    check("HTML lạ → rỗng, không nổ", amz.parse_search("<html>x</html>", "amazon.de"), ([], None))

    print("\n=== 1688 (JSON nhúng) ===")
    items, psrc = ali.parse_search(ALI_JSON_HTML)
    check("đọc 2 offer", len(items), 2)
    check("nhánh parser", psrc, "embedded-json")
    o = items[0]
    check("giá thấp 12.50 CNY → 1250", o["price_min_minor"], 1250)
    check("giá cao 18.00 CNY → 1800 (giá bậc thang)", o["price_max_minor"], 1800)
    check("nhà cung cấp → seller", o["seller"], "义乌市小家电有限公司")
    check("sales_volume — 1688 CÓ công bố", o["sales_volume"], 3200)
    check("đọc được cả tên trường thay thế (company)", items[1]["seller"], "深圳风扇厂")

    items, psrc = ali.parse_search(ALI_DOM_HTML)
    check("không có JSON → rơi xuống nhánh DOM", psrc, "dom-offer-link")
    check("id lấy từ URL offer", items[0]["external_id"], "777888999000")

    print("\n=== TAOBAO (g_page_config) ===")
    items, psrc = tb.parse_search(TAOBAO_HTML)
    check("đọc 2 sản phẩm", len(items), 2)
    check("nhánh parser", psrc, "g_page_config")
    t = items[0]
    check("tiêu đề đã bỏ thẻ <span> bôi đậm", t["title"], "便携榨汁机")
    check("giá 29.90 CNY → 2990", t["price_min_minor"], 2990)
    check("'1.2万人付款' → 12000 (KHÔNG phải 1)", t["sales_volume"], 12000)
    check("'338人付款' → 338", items[1]["sales_volume"], 338)
    check("pic_url '//...' → 'https://...'", t["image_refs"], ["https://img.alicdn.com/a.jpg"])
    check("phân trang theo offset: trang 3 → s=88", tb.search_url("x", 3).endswith("&s=88"), True)

    print("\n=== Nhận diện bị chặn ===")
    check("trang /punish của 1688", looks_blocked("<html>.../punish...</html>"), True)
    check("x5secdata", looks_blocked("<html>x5secdata=abc</html>"), True)
    check("login.taobao.com", looks_blocked("<html>login.taobao.com</html>"), True)
    check("captcha Amazon", looks_blocked("Enter the characters you see below"), True)
    check("trang 1688 bình thường KHÔNG bị nhận nhầm", looks_blocked(ALI_JSON_HTML), False)
    check("trang Taobao bình thường KHÔNG bị nhận nhầm", looks_blocked(TAOBAO_HTML), False)


# ══ 2. Adapter + vòng lặp phân trang ════════════════════════════════════════


def _ok(html: str) -> browser_fetch.BrowserResponse:
    return browser_fetch.BrowserResponse(None, 200, html, 10, fingerprint_seed=123)


def _blocked() -> browser_fetch.BrowserResponse:
    return browser_fetch.BrowserResponse(
        Outcome.BLOCKED, 200, "", 10, error="BLOCKED", fingerprint_seed=123
    )


def _card(asin: str, price: str) -> str:
    return (
        f'<div data-asin="{asin}"><h2><a href="/dp/{asin}"><span>SP {asin}</span></a></h2>'
        f'<span class="a-price"><span class="a-offscreen">{price} €</span></span></div>'
    )


def _run(adapter, params: dict, pages: dict):
    """Chạy adapter với tầng trình duyệt giả. Trả (FetchResult, số trang đã gọi)."""
    calls: list[str] = []

    async def fake_fetch(url, *, source, wait_for=None, settle_ms=0, **kw):
        calls.append(url)
        return pages[len(calls)]

    original = browser_fetch.fetch_page
    browser_fetch.fetch_page = fake_fetch
    try:
        req = FetchRequest(source="x", capability=Capability.SEARCH_KEYWORD, params=params)
        return asyncio.run(adapter.fetch(req, None)), calls
    finally:
        browser_fetch.fetch_page = original


def check_adapters() -> None:
    amazon = AmazonBrowserAdapter()
    base = {"query": "q", "marketplace": "amazon.de"}

    print("\n=== Vòng lặp phân trang ===")
    res, _ = _run(amazon, {**base, "max_pages": 2}, {1: _ok(_card("A1", "10,00")), 2: _ok(_card("A2", "20,00"))})
    check("gộp sản phẩm của mọi trang", len(res.items), 2)
    check("ghi lại fingerprint seed để truy vết", res.meta["fingerprint_seed"], 123)

    res, calls = _run(amazon, {**base, "max_pages": 5}, {1: _ok(_card("A1", "10,00")), 2: _ok("<html>rỗng</html>")})
    check("hết sản phẩm thì dừng sớm, không chạy đủ max_pages", len(calls), 2)

    res, _ = _run(amazon, {**base, "max_pages": 3}, {1: _ok(_card("A1", "10,00")), 2: _blocked()})
    check("gãy giữa chừng vẫn giữ phần đã lấy → OK", res.outcome, Outcome.OK)
    check("và xoá error đi", res.error, None)

    res, calls = _run(amazon, {**base, "max_pages": 3}, {1: _blocked()})
    check("trang đầu bị chặn → BLOCKED", res.outcome, Outcome.BLOCKED)
    check("dừng ngay, không thử trang sau", len(calls), 1)

    print("\n=== EMPTY vs PARSE_FAIL (chỗ dễ sai nhất) ===")
    res, _ = _run(amazon, {**base, "max_pages": 2}, {1: _ok("<html>No results for xyz</html>")})
    check("trang 'không có kết quả' → EMPTY", res.outcome, Outcome.EMPTY)
    check("EMPTY không kèm error — đây là câu trả lời hợp lệ", res.error, None)

    res, _ = _run(amazon, {**base, "max_pages": 2}, {1: _ok("<html><div class='layout-moi'>x</div></html>")})
    check("trang lạ, không dấu hiệu chặn → PARSE_FAIL", res.outcome, Outcome.PARSE_FAIL)
    check("PARSE_FAIL chỉ rõ file cần sửa", "amazon/normalize.py" in (res.error or ""), True)

    print("\n=== Taobao dùng chung vòng lặp đó ===")
    res, _ = _run(TaobaoBrowserAdapter(), {"query": "榨汁机", "max_pages": 1}, {1: _ok(TAOBAO_HTML)})
    check("outcome OK", res.outcome, Outcome.OK)
    check("sales 1.2万 → 12000 xuyên suốt tới adapter", res.items[0]["sales_volume"], 12000)

    print("\n=== Tier vendor tự loại khi chưa cấu hình ===")
    import app.config as cfg

    cfg.settings.amazon_apify_actor = ""
    cfg.settings.alibaba_aggregator_base = ""
    check("Amazon vendor — thiếu actor", AmazonVendorAdapter().is_available(), False)
    check("Taobao vendor — thiếu aggregator", TaobaoVendorAdapter().is_available(), False)


def check_vendor_mapping() -> None:
    """Map output THẬT của actor Apify (lấy từ README của junglee/Amazon-crawler)."""
    from app.sources.amazon.vendor import _map_item

    print("\n=== Map output thật của actor Amazon ===")
    row = _map_item(AMAZON_ACTOR_ITEM, "amazon.de", "EUR")
    check("lấy được ASIN", row["external_id"], "B09X7MPX8L")
    check("giá nằm trong object lồng {value,currency} → 14550", row["price_min_minor"], 14550)
    check(
        "currency lấy theo marketplace, KHÔNG lấy ký hiệu '$' của actor",
        row["currency"],
        "EUR",
    )
    check("stars → rating", row["rating"], 4.8)
    check("reviewsCount → review_count", row["review_count"], 36704)
    check("brand", row["brand"], "SanDisk")
    check("inStock → available", row["available"], True)
    check("thumbnailImage → image_refs", row["image_refs"], [AMAZON_ACTOR_ITEM["thumbnailImage"]])
    check("sales_volume vẫn None — Amazon không công bố", row["sales_volume"], None)
    check("item không có asin → bỏ qua, không nổ", _map_item({"title": "x"}, "amazon.de", "EUR"), None)


def main() -> int:
    check_parsers()
    check_adapters()
    check_vendor_mapping()

    print("\n" + "=" * 70)
    if _fails:
        print(f"{len(_fails)} CASE HỎNG:")
        for f in _fails:
            print("  - " + f)
        return 1
    print("TẤT CẢ PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
