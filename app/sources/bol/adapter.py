"""Adapter T2 cho Bol.com — lớp vỏ mỏng quanh `services/bol_client.py`.

`needs_identity` để `False` dù thực tế đang bị chặn: engine G1 chưa có identity pool,
khai `True` sẽ khiến nguồn này bị bỏ qua hoàn toàn và không ghi được `crawl_attempts`
nào — mất luôn bằng chứng "đang bị chặn". Thà cứ thử, bị `BLOCKED`, và có số liệu.
Đổi sang `True` khi G2 (`app/crawl/identity.py`) cấp được proxy NL/BE thật.
"""

from __future__ import annotations

from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.services import bol_client


class BolScrapeAdapter:
    tier = "browser"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        result = await bol_client.search(
            req.params["query"],
            max_pages=req.params.get("max_pages", 3),
            country_path=req.params.get("country_path", "/nl/nl"),
        )
        return FetchResult(
            outcome=result.outcome,
            items=result.products,
            tier_used=self.tier,
            latency_ms=result.latency_ms,
            error=result.error,
            meta={
                "pages_fetched": result.pages_fetched,
                "parse_source": result.parse_source,
            },
        )
