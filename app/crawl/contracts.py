"""Hợp đồng chung cho mọi adapter nguồn — điểm mấu chốt của toàn bộ thiết kế.

Mọi tier (T0 official / T1 vendor / T2 tự scrape / adapter giả để test) đều nhận
`FetchRequest` và trả `FetchResult` CÙNG một kiểu. Nhờ vậy `engine.py` fallback
giữa các tier mà tầng gọi (router/job) không cần biết dữ liệu tới từ đâu.

Xem docs/DEV-Design-Crawl-Engine.md §7.2.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.crawl.outcomes import Outcome

# `Identity` (proxy + fingerprint + cookie jar) thuộc G2 — app/crawl/identity.py chưa
# tồn tại. Giữ chỗ bằng alias để chữ ký `SourceAdapter.fetch` đúng thiết kế ngay từ
# G1, không phải sửa lại mọi adapter khi G2 thêm class thật.
Identity = Any


class Capability(StrEnum):
    SEARCH_KEYWORD = "search_keyword"
    SCAN = "scan"
    """Quét trọn danh mục của MỘT cửa hàng đã biết (Shopify `/products.json`).

    Khác `SEARCH_KEYWORD` ở chỗ không có truy vấn: đầu vào là `shop_domain`, đầu ra
    là toàn bộ catalog. Tách riêng vì chuỗi tier và policy của hai việc này khác nhau."""
    SEARCH_IMAGE = "search_image"
    """Bắt buộc cho 1688 — SRS Bước 4. Chưa có adapter thật nào dùng ở G1."""
    DETAIL = "detail"
    REVIEWS = "reviews"


def make_idempotency_key(source: str, capability: Capability, params: dict) -> str:
    """`sha256(source | capability | canonical(params))` — xem docs §9.3.

    `sort_keys=True` để cùng một bộ params nhưng khác thứ tự khai báo vẫn ra cùng
    key — nếu không, cache coi hai request giống hệt nhau là hai request khác nhau.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    raw = f"{source}|{capability}|{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FetchRequest:
    source: str
    capability: Capability
    params: dict
    country: str = ""
    idempotency_key: str = ""
    run_id: str | None = None
    """Để quy chi phí về đúng một lần research bên `levelup_be` — xem §9.4."""

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            object.__setattr__(
                self, "idempotency_key", make_idempotency_key(self.source, self.capability, self.params)
            )


@dataclass
class FetchResult:
    outcome: Outcome
    items: list[dict] = field(default_factory=list)
    """ĐÃ chuẩn hoá — không phải payload thô của vendor. Xem ràng buộc ở §7.2."""
    raw_ref: str | None = None
    """Trỏ sang `api_audit_logs.request_id` để dựng lại khi cần, không đọc trong luồng chính."""
    meta: dict = field(default_factory=dict)
    """Metadata vận hành riêng từng nguồn mà `items` không tải được: `pages_fetched`,
    `parse_source` (Bol đọc json-ld hay CSS), `currency` (Shopify hỏi /meta.json),
    `comments` (Reddit trả 2 loại thực thể)...

    KHÔNG để dữ liệu nghiệp vụ chính ở đây — thực thể đã chuẩn hoá thuộc về `items`,
    nếu không thì tầng trên lại phải biết mỗi nguồn nhét gì vào đâu, đúng thứ mà
    hợp đồng chung này sinh ra để tránh."""
    tier_used: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
    http_status: int | None = None
    error: str | None = None
    stale: bool = False
    """True khi kết quả tới từ cache/T3, không phải một lần fetch thật vừa xảy ra."""


class SourceAdapter(Protocol):
    tier: str
    capabilities: set[Capability]
    needs_identity: bool
    """True thì cần `Identity` từ G2 (`app/crawl/identity.py`) — engine G1 sẽ nhảy
    sang tier kế tiếp nếu chưa có hệ thống identity nào cấp được instance thật."""

    async def fetch(self, req: FetchRequest, identity: Identity | None) -> FetchResult: ...

    def is_available(self) -> bool:
        """Tier này có đủ điều kiện để thử không (đã cấu hình credential/key chưa).

        KHÔNG bắt buộc — adapter không định nghĩa thì mặc định là có. Dùng để loại
        tier ra khỏi chuỗi NGAY TỪ ĐẦU thay vì để nó chạy rồi thất bại: một tier
        thiếu credential mà vẫn thử sẽ đẻ ra một dòng `crawl_attempts` rác ở MỌI lần
        crawl, làm hỏng chính con số tỉ lệ thành công mà bảng đó sinh ra để đo.
        """
        ...
