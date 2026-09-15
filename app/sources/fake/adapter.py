"""Adapter giả — không gọi mạng, chỉ đọc fixture hoặc trả outcome đã định sẵn.

Lý do tồn tại: chứng minh `engine.py` (fallback theo tier, retry, ghi `crawl_attempts`)
chạy đúng bằng dữ liệu rẻ/tất định, TRƯỚC KHI tốn một đồng nào cho vendor thật —
đúng cột mốc G1 trong docs/DEV-Design-Crawl-Engine.md §11.

KHÔNG dùng các adapter này ngoài `scripts/demo_crawl_engine.py` và test — chúng
không đại diện cho bất kỳ nguồn dữ liệu thật nào.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.crawl.outcomes import Outcome

_FIXTURE = Path(__file__).parent / "fixtures" / "demo_products.json"


class FakeBlockedAdapter:
    """Luôn bị chặn — mô phỏng tier T0 hết hạn/bị cấm để buộc engine nhảy tier."""

    tier = "fake_t0_official"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        return FetchResult(
            outcome=Outcome.BLOCKED,
            tier_used=self.tier,
            latency_ms=5,
            error="fake: captcha giả lập",
        )


class FakeVendorAdapter:
    """Đọc fixture tĩnh, luôn thành công — mô phỏng tier T1 (vendor đã trả tiền)."""

    tier = "fake_t1_vendor"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False
    cost_per_call_usd = 0.001

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        items = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        return FetchResult(
            outcome=Outcome.OK,
            items=items,
            tier_used=self.tier,
            cost_usd=self.cost_per_call_usd,
            latency_ms=12,
        )


class FakeEmptyAdapter:
    """200 hợp lệ, 0 kết quả — kiểm chứng `classify.py` tôn trọng EMPTY, không coi là lỗi."""

    tier = "fake_t0_official"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        return FetchResult(outcome=Outcome.OK, items=[], tier_used=self.tier, latency_ms=8)


class FakeFlakyAdapter:
    """Lỗi mạng ở lần thử đầu, thành công ở lần sau — kiểm chứng engine retry ĐÚNG
    TIER khi outcome là `UPSTREAM_ERROR` (khác với `BLOCKED`, vốn phải nhảy tier).

    Có state nội bộ (`_calls`) — chấp nhận được vì đây CHỈ dùng cho demo/test, không
    phải adapter thật chạy production."""

    tier = "fake_t0_official"
    capabilities = {Capability.SEARCH_KEYWORD}
    needs_identity = False

    def __init__(self) -> None:
        self._calls = 0

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        self._calls += 1
        if self._calls == 1:
            return FetchResult(
                outcome=Outcome.UPSTREAM_ERROR, tier_used=self.tier, error="fake: timeout giả lập"
            )
        items = json.loads(_FIXTURE.read_text(encoding="utf-8"))[:1]
        return FetchResult(outcome=Outcome.OK, items=items, tier_used=self.tier, latency_ms=9)
