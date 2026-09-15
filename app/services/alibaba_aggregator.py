"""Client cho aggregator dữ liệu Alibaba (1688 · Taobao) — tầng T1.

`docs/DEV-Design-Crawl-Engine.md` gọi đây là nguồn **nên mua dứt khoát nhất**: cổng
chính thức của Alibaba đòi pháp nhân + xác thực thực danh tại Trung Quốc, thực tế
không khả thi với công ty Việt Nam; còn tự scrape thì vướng đăng nhập, slider captcha
và device token `x5sec`. Aggregator TQ (onebound/万邦 và tương đương) đã giải sẵn cả
ba thứ đó và bán lại, kèm `item_search_img` phục vụ tìm nguồn hàng bằng ảnh (SRS
Bước 4.1) — thứ mà tự scrape gần như không làm nổi.

Địa chỉ + key để trong `.env`: mấy nhà này API na ná nhau nên đổi nhà cung cấp phải
là sửa một dòng env, không phải một lần deploy. Docs §2 cũng yêu cầu **chạy thử 100
request thật với từng vendor rồi mới ký hợp đồng năm** — không đổi được bằng env thì
việc so sánh đó không làm nổi.

Chưa cấu hình key thì adapter gọi `is_available()` sẽ loại tier ngay từ đầu, không
sinh ra dòng `crawl_attempts` rác nào.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.crawl.outcomes import Outcome

#: Trường chứa mảng bản ghi trong phản hồi. Mỗi nhà bọc một kiểu, thử lần lượt.
_ITEM_CONTAINERS = ("items", "result", "data")
_ITEM_KEYS = ("item", "items", "list", "products")


class AggregatorResult:
    """Kết quả một lần gọi: outcome đã phân loại + payload thô để adapter tự map."""

    def __init__(
        self,
        outcome: Outcome,
        items: list[dict] | None = None,
        latency_ms: int = 0,
        error: str | None = None,
        raw: Any = None,
    ) -> None:
        self.outcome = outcome
        self.items = items or []
        self.latency_ms = latency_ms
        self.error = error
        self.raw = raw


def is_configured() -> bool:
    return bool(settings.alibaba_aggregator_base and settings.alibaba_aggregator_key)


async def call(api_name: str, params: dict[str, Any], platform: str = "1688") -> AggregatorResult:
    """Gọi một API của aggregator, trả về danh sách bản ghi thô đã lấy ra khỏi vỏ bọc.

    `platform` ('1688' | 'taobao') được thay vào chỗ `{platform}` của
    `ALIBABA_AGGREGATOR_BASE` — các nhà này tách endpoint theo sàn. Một biến env phục
    vụ cả hai nguồn; base nào không có chỗ thay thì dùng nguyên văn.

    KHÔNG map sang hình dạng `ecom_products` ở đây: 1688 và Taobao trả tên trường khác
    nhau dù chung một nhà, nên việc map thuộc về adapter của từng nguồn.
    """
    if not is_configured():
        return AggregatorResult(
            Outcome.NOT_AVAILABLE,
            error="Chưa cấu hình ALIBABA_AGGREGATOR_BASE / ALIBABA_AGGREGATOR_KEY",
        )

    base = settings.alibaba_aggregator_base.replace("{platform}", platform)

    query = {
        "api_name": api_name,
        "key": settings.alibaba_aggregator_key,
        "secret": settings.alibaba_aggregator_secret,
        "cache": "no",
        "lang": "zh-CN",
        **params,
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.crawl_timeout_s) as client:
            resp = await client.get(base, params=query)
    except httpx.TimeoutException as exc:
        return AggregatorResult(Outcome.UPSTREAM_ERROR, latency_ms=_ms(start), error=f"timeout: {exc}")
    except httpx.RequestError as exc:
        return AggregatorResult(Outcome.UPSTREAM_ERROR, latency_ms=_ms(start), error=f"network: {exc}")

    latency = _ms(start)

    if resp.status_code == 401 or resp.status_code == 403:
        return AggregatorResult(Outcome.BLOCKED, latency_ms=latency, error=f"HTTP {resp.status_code} — key sai hoặc bị khoá")
    if resp.status_code >= 500:
        return AggregatorResult(Outcome.UPSTREAM_ERROR, latency_ms=latency, error=f"HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError:
        return AggregatorResult(
            Outcome.PARSE_FAIL, latency_ms=latency, error="Phản hồi không phải JSON"
        )

    # Các nhà này hay trả HTTP 200 kèm mã lỗi trong thân — hết credit là ca quan trọng
    # nhất vì engine phải nhảy sang tier sau chứ không phải retry.
    error_text = str(payload.get("error") or payload.get("error_code") or "").lower()
    if error_text and error_text not in ("0", "none", ""):
        outcome = (
            Outcome.QUOTA_EXHAUSTED
            if any(k in error_text for k in ("quota", "credit", "余额", "limit"))
            else Outcome.UPSTREAM_ERROR
        )
        return AggregatorResult(outcome, latency_ms=latency, error=str(payload.get("reason") or error_text), raw=payload)

    items = _unwrap(payload)
    return AggregatorResult(
        Outcome.OK if items else Outcome.EMPTY, items=items, latency_ms=latency, raw=payload
    )


def _unwrap(payload: Any) -> list[dict]:
    """Lấy mảng bản ghi ra khỏi lớp vỏ `{"items": {"item": [...]}}`."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []

    for container in _ITEM_CONTAINERS:
        node = payload.get(container)
        if isinstance(node, list):
            return [x for x in node if isinstance(x, dict)]
        if isinstance(node, dict):
            for key in _ITEM_KEYS:
                inner = node.get(key)
                if isinstance(inner, list):
                    return [x for x in inner if isinstance(x, dict)]
                if isinstance(inner, dict):
                    return [inner]
    return []


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
