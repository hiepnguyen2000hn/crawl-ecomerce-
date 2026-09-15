"""Taobao — tier T2, tự scrape bằng CloakBrowser.

⚠️ `docs/DEV-Design-Crawl-Engine.md` xếp Taobao là **khó nhất trong nhóm**: ngoài mọi
thứ 1688 có (slider captcha, device token `x5sec`, proxy phải residential Trung Quốc
đại lục), Taobao còn bắt đăng nhập sớm hơn nhiều — trang tìm kiếm thường đá thẳng sang
`login.taobao.com` với khách vãng lai.

Vì vậy đừng kỳ vọng tier này ra dữ liệu khi chưa có identity pool (G2) kèm tài khoản
thật. Giữ nó trong chuỗi để **đo được mức độ bị chặn**, và để khi G2 xong thì chỉ phải
cắm proxy vào chứ không phải viết lại adapter.
"""

from __future__ import annotations

from importlib.util import find_spec

from app.crawl import paged_search
from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.sources.taobao import normalize

_RESULT_SELECTOR = "a[href*='item.htm']"

#: Sàn TQ render chậm — cùng lý do với 1688.
_SETTLE_MS = 4000


class TaobaoBrowserAdapter:
    tier = "browser"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False
    """Xem docstring đầu file — để False để còn ghi được bằng chứng bị chặn."""

    def is_available(self) -> bool:
        return find_spec("playwright") is not None

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params

        paged = await paged_search.run(
            source="taobao",
            max_pages=int(p.get("max_pages", 2)),
            url_for=lambda page: normalize.search_url(p["query"], page),
            parse=lambda html: normalize.parse_search(html),
            looks_empty=normalize.looks_like_no_results,
            wait_for=_RESULT_SELECTOR,
            settle_ms=_SETTLE_MS,
            parse_fail_hint=(
                "Kiểm tra _DATA_VARS trong app/sources/taobao/normalize.py bằng "
                "GET /api/v1/ecom/taobao/probe — nhiều khả năng bị đá sang trang đăng nhập."
            ),
        )

        return FetchResult(
            outcome=paged.outcome,
            items=paged.items,
            tier_used=self.tier,
            latency_ms=paged.latency_ms,
            error=paged.error,
            meta={
                "pages_fetched": paged.pages_fetched,
                "parse_source": paged.parse_source,
                "fingerprint_seed": paged.fingerprint_seed,
            },
        )
