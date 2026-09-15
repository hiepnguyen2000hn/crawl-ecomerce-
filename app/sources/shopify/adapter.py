"""Adapter T0 cho Shopify — lớp vỏ mỏng quanh `services/shopify_client.py`.

Cố tình KHÔNG đụng vào logic fetch/parse bên trong client: nó đang chạy tốt trên
dữ liệu thật. Việc duy nhất ở đây là dịch `ShopifyScanResult` sang `FetchResult`
để `engine.py` fallback/đo đạc được như mọi nguồn khác.
"""

from __future__ import annotations

from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.services import shopify_client


class ShopifyOfficialAdapter:
    tier = "official"
    capabilities = {Capability.SCAN}
    needs_identity = False

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        result = await shopify_client.scan_store(
            req.params["shop_domain"],
            max_pages=req.params.get("max_pages", 10),
            currency=req.params.get("currency"),
        )
        return FetchResult(
            outcome=result.outcome,
            items=result.products,
            tier_used=self.tier,
            latency_ms=result.latency_ms,
            error=result.error,
            meta={
                "shop_domain": result.shop_domain,
                "currency": result.currency,
                "pages_fetched": result.pages_fetched,
                "raw_sample": result.raw_sample,
            },
        )
