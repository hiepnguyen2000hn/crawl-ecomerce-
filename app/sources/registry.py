"""Nguồn → chuỗi adapter theo thứ tự tier ưu tiên (§7.1).

Thứ tự khai báo trong `_REGISTRY` là thứ tự fallback mặc định. `CrawlSourcePolicy.tier_chain`
(nếu có row trong DB) sẽ **lọc và sắp lại** chuỗi đó — cách đổi đường đi của một nguồn
bằng một câu `UPDATE` thay vì phải deploy lại (§5).
"""

from __future__ import annotations

from app.crawl.contracts import Capability, SourceAdapter
from app.sources.fake.adapter import (
    FakeBlockedAdapter,
    FakeEmptyAdapter,
    FakeFlakyAdapter,
    FakeVendorAdapter,
)
from app.sources.alibaba_1688.browser import Alibaba1688BrowserAdapter
from app.sources.alibaba_1688.vendor import Alibaba1688VendorAdapter
from app.sources.amazon.browser import AmazonBrowserAdapter
from app.sources.amazon.vendor import AmazonVendorAdapter
from app.sources.bol.adapter import BolScrapeAdapter
from app.sources.reddit.adapter import RedditApifyAdapter, RedditOAuthAdapter
from app.sources.shopify.adapter import ShopifyOfficialAdapter
from app.sources.taobao.browser import TaobaoBrowserAdapter
from app.sources.taobao.vendor import TaobaoVendorAdapter

_REGISTRY: dict[str, list[SourceAdapter]] = {
    # ── Nguồn thật ────────────────────────────────────────────────────────────
    "shopify": [ShopifyOfficialAdapter()],
    "bol": [BolScrapeAdapter()],
    "reddit": [RedditOAuthAdapter(), RedditApifyAdapter()],
    # Ba nguồn dưới đây đặt vendor TRƯỚC browser theo quyết định D1 của
    # docs/DEV-Design-Crawl-Engine.md: anti-bot của chúng ở mức đầu tư hàng chục
    # triệu đô, tự scrape đạt ~70% tuần đầu rồi tụt dần — chi phí thật nằm ở công dev
    # đi sửa mỗi lần sàn đổi, không phải tiền proxy. Tier browser giữ lại làm dự phòng
    # và để đo mức độ bị chặn; chưa cấu hình key vendor thì `is_available()` của nó tự
    # loại tier vendor ra khỏi chuỗi, không đẻ ra `crawl_attempts` rác.
    "amazon": [AmazonVendorAdapter(), AmazonBrowserAdapter()],
    "alibaba_1688": [Alibaba1688VendorAdapter(), Alibaba1688BrowserAdapter()],
    "taobao": [TaobaoVendorAdapter(), TaobaoBrowserAdapter()],
    # ── Adapter giả, chỉ dùng cho scripts/demo_crawl_engine.py ────────────────
    "fake_demo": [FakeBlockedAdapter(), FakeVendorAdapter()],
    "fake_empty_demo": [FakeEmptyAdapter()],
    "fake_flaky_demo": [FakeFlakyAdapter()],
    "fake_demo_cached": [FakeVendorAdapter()],
}


def _always() -> bool:
    """Mặc định cho adapter không khai `is_available` — xem contracts.SourceAdapter."""
    return True


def chain(
    source: str, capability: Capability, tier_chain: tuple[str, ...] = ()
) -> list[SourceAdapter]:
    """Chuỗi adapter phục vụ `capability` của `source`, đã áp policy.

    `tier_chain` rỗng → giữ nguyên thứ tự khai báo. Có giá trị → chỉ lấy đúng những
    tier được liệt kê, theo đúng thứ tự đó; tier lạ (gõ sai, hoặc adapter đã gỡ) bị
    bỏ qua im lặng thay vì làm hỏng cả lần crawl.
    """
    adapters = [
        a
        for a in _REGISTRY.get(source, [])
        if capability in a.capabilities and getattr(a, "is_available", _always)()
    ]
    if not tier_chain:
        return adapters
    by_tier = {a.tier: a for a in adapters}
    return [by_tier[t] for t in tier_chain if t in by_tier]
