"""Hai adapter Reddit — T0 OAuth và T1 Apify, cùng một hình dạng kết quả.

`settings.reddit_provider` ('auto' | 'oauth' | 'apify') vẫn được tôn trọng qua
`is_available()` để không phá cấu hình `.env` đang dùng. Cách ưu tiên về lâu dài là
`crawl_source_policies.tier_chain` trong DB — đổi được lúc 2 giờ sáng bằng một câu
UPDATE, không cần deploy.
"""

from __future__ import annotations

from app.config import settings
from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.services import reddit_client


def _as_result(voc, tier: str) -> FetchResult:
    """`RedditVocResult` → `FetchResult`.

    `threads` là thực thể chính nên đi vào `items`; `comments` là thực thể phụ của
    cùng một lần thu thập nên đi vào `meta` (xem ghi chú ở `FetchResult.meta`).
    """
    return FetchResult(
        outcome=voc.outcome,
        items=voc.threads,
        tier_used=tier,
        cost_usd=voc.cost_usd,
        latency_ms=voc.latency_ms,
        error=voc.error,
        meta={
            "comments": voc.comments,
            "filter_report": voc.filter_report,
            "top_subreddits": voc.top_subreddits,
        },
    )


class RedditOAuthAdapter:
    tier = "official"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False

    def is_available(self) -> bool:
        if (settings.reddit_provider or "auto").lower() == "apify":
            return False
        return bool(settings.reddit_client_id and settings.reddit_client_secret)

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params
        voc = await reddit_client.collect_via_oauth(
            p["keywords"],
            p.get("timeframe", "year"),
            p.get("threads_per_keyword", 50),
            p.get("comment_threads", 10),
            p.get("comments_per_thread", 60),
        )
        return _as_result(voc, self.tier)


class RedditApifyAdapter:
    tier = "vendor:apify"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False

    def is_available(self) -> bool:
        if (settings.reddit_provider or "auto").lower() == "oauth":
            return False
        return bool(settings.apify_token)

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params
        keywords = p["keywords"]
        voc = await reddit_client.collect_via_apify(
            keywords,
            p.get("timeframe", "year"),
            p.get("threads_per_keyword", 50) * len(keywords),
            sort=p.get("sort", "relevance"),
            subreddits=p.get("subreddits"),
            min_relevance=p.get("min_relevance", 0.4),
        )
        return _as_result(voc, self.tier)
