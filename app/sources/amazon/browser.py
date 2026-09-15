"""Amazon — tier T2, tự scrape bằng CloakBrowser.

⚠️ Đây là tier **dự phòng**, không phải tier chính. `docs/DEV-Design-Crawl-Engine.md`
quyết định D1: với Amazon/1688/Taobao thì **mua dữ liệu** (T1 vendor), tự scrape chỉ
để đỡ khi vendor hỏng hoặc chưa ký được hợp đồng. Lý do không phải kỹ thuật mà là chi
phí bảo trì: tự scrape đạt ~70% tuần đầu rồi tụt dần, và tiền thật tốn vào công dev đi
sửa mỗi lần Amazon đổi layout, chứ không phải tiền proxy.

Chưa có proxy residential khớp marketplace (G2) thì tier này bị chặn là chuyện bình
thường. Vẫn để nó chạy và ghi `crawl_attempts` thay vì bỏ qua — bị chặn mà đo được
vẫn hơn không có số liệu nào, và đó là bằng chứng để bảo vệ đề xuất mua T1.
"""

from __future__ import annotations

from importlib.util import find_spec

from app.crawl import paged_search
from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.sources.amazon import normalize

#: Thẻ sản phẩm đầu tiên — bằng chứng trang đã render xong danh sách.
_RESULT_SELECTOR = "div[data-asin]"


class AmazonBrowserAdapter:
    tier = "browser"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False
    """Để False dù thực tế rất cần identity: engine G1 bỏ qua hẳn adapter khai True,
    và bỏ qua thì không sinh được dòng `crawl_attempts` nào — mất luôn bằng chứng
    "Amazon đang chặn ta". Đổi sang True khi G2 cấp được proxy residential thật.
    Cùng lý do với BolScrapeAdapter."""

    def is_available(self) -> bool:
        """Không cài trình duyệt thì loại tier NGAY, đừng để nó chạy rồi thất bại.

        Một tier thiếu điều kiện mà vẫn thử sẽ đẻ ra một dòng `crawl_attempts` rác ở
        MỌI lần crawl, làm hỏng chính con số tỉ lệ thành công mà bảng đó sinh ra để đo.
        """
        return find_spec("playwright") is not None

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params
        marketplace = p.get("marketplace") or normalize.DEFAULT_MARKETPLACE

        paged = await paged_search.run(
            source="amazon",
            max_pages=int(p.get("max_pages", 2)),
            url_for=lambda page: normalize.search_url(p["query"], marketplace, page),
            parse=lambda html: normalize.parse_search(html, marketplace),
            looks_empty=normalize.looks_like_no_results,
            wait_for=_RESULT_SELECTOR,
            parse_fail_hint="Kiểm tra lại bộ chọn trong app/sources/amazon/normalize.py.",
        )

        return FetchResult(
            outcome=paged.outcome,
            items=paged.items,
            tier_used=self.tier,
            latency_ms=paged.latency_ms,
            error=paged.error,
            meta={
                "marketplace": marketplace,
                "pages_fetched": paged.pages_fetched,
                "parse_source": paged.parse_source,
                "fingerprint_seed": paged.fingerprint_seed,
            },
        )
