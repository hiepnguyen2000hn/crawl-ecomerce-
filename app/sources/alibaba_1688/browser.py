"""1688 — tier T2, tự scrape bằng CloakBrowser.

⚠️ Tier dự phòng. Theo `docs/DEV-Design-Crawl-Engine.md`, 1688 là **nguồn nên mua dứt
khoát nhất** trong cả nhóm: bắt đăng nhập, slider captcha, device token `x5sec`, và
proxy phải là residential **Trung Quốc đại lục** — proxy ngoài TQ bị giới hạn hoặc bị
chuyển hướng. G1 chưa có identity pool nên chưa cấp được proxy nào.

Nói thẳng để không ai kỳ vọng nhầm: **chạy tier này lúc chưa có proxy TQ thì gần như
chắc chắn ra `BLOCKED`.** Vẫn giữ nó trong chuỗi vì một lần `BLOCKED` được ghi vào
`crawl_attempts` là một dòng số liệu thật, còn bỏ qua thì không có gì cả — và chính
những dòng đó là bằng chứng để chốt việc mua T1.
"""

from __future__ import annotations

from importlib.util import find_spec

from app.crawl import paged_search
from app.crawl.contracts import Capability, FetchRequest, FetchResult, Identity
from app.sources.alibaba_1688 import normalize

#: 1688 dựng danh sách bằng JS — chờ link offer đầu tiên xuất hiện mới chắc đã render.
_RESULT_SELECTOR = "a[href*='detail.1688.com/offer/']"

#: Sàn TQ render chậm hơn hẳn; đồng nghiệp đo trên script multi-profile cũng phải chờ
#: ~4s cho 1688/Shopee/Lazada. Chờ thiếu thì HTML về lúc trang còn trống và ta sẽ
#: chẩn đoán nhầm thành PARSE_FAIL.
_SETTLE_MS = 4000


class Alibaba1688BrowserAdapter:
    tier = "browser"
    capabilities = {Capability.SEARCH_KEYWORD}
    """Cố tình KHÔNG khai `SEARCH_IMAGE`: tìm bằng ảnh trên 1688 đòi hỏi upload ảnh
    qua luồng riêng, không phải mở một URL tìm kiếm. Tier vendor có sẵn `item_search_img`
    nên việc đó thuộc về `vendor.py`. Khai bừa ở đây sẽ khiến engine chọn tier này cho
    một việc nó không làm được, rồi thất bại một cách khó hiểu."""
    needs_identity = False
    """Xem docstring đầu file — để False để còn ghi được bằng chứng bị chặn."""

    def is_available(self) -> bool:
        return find_spec("playwright") is not None

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult:
        p = req.params

        paged = await paged_search.run(
            source="alibaba_1688",
            max_pages=int(p.get("max_pages", 2)),
            url_for=lambda page: normalize.search_url(p["query"], page),
            parse=lambda html: normalize.parse_search(html),
            looks_empty=normalize.looks_like_no_results,
            wait_for=_RESULT_SELECTOR,
            settle_ms=_SETTLE_MS,
            parse_fail_hint=(
                "Nhiều khả năng tên biến JS đã đổi — kiểm tra _DATA_VARS trong "
                "app/sources/alibaba_1688/normalize.py bằng GET /api/v1/ecom/1688/probe."
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
                # 'embedded-json' là nhánh bền; tụt xuống 'dom-offer-link' nghĩa là
                # object JS đã đổi tên và ta đang đi đường dễ vỡ hơn — cần biết sớm.
                "parse_source": paged.parse_source,
                "fingerprint_seed": paged.fingerprint_seed,
            },
        )
