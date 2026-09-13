"""Lớp gọi HTTP dùng chung cho các connector tự fetch (không qua vendor).

Gộp ba thứ mà mỗi connector đều cần và rất dễ viết lại sai:
  - User-Agent thật (nhiều nền tảng chặn thẳng UA mặc định của httpx)
  - nhịp tối thiểu giữa hai request cùng một host
  - phân loại kết quả theo `crawl.outcomes` ngay tại biên

Nhịp hiện dùng khoá trong process. Khi scale lên nhiều worker container thì phải
chuyển sang khoá Redis — xem docs/DEV-Design-Crawl-Engine.md §5.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.crawl.outcomes import Outcome, classify_status

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

#: Bộ header mô phỏng điều hướng thật của trình duyệt.
#:
#: Đây KHÔNG phải chi tiết vặt: đo trên Bol.com, chỉ gửi User-Agent thì bị **403**,
#: gửi đủ bộ này thì **200**. Anti-bot hiện đại chấm điểm cả cụm header (thứ tự,
#: sự có mặt của Sec-Fetch-*, Sec-Ch-Ua), không chỉ riêng UA. Thiếu chúng là
#: tín hiệu "client tự động" rõ hơn cả một UA lạ.
#:
#: `Accept-Language` để mặc định tiếng Anh; connector nào nhắm thị trường khác
#: thì tự truyền đè (Bol.com dùng nl-NL) — ngôn ngữ phải khớp geo, lệch là cờ đỏ.
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

_last_hit: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = {}


@dataclass
class FetchResponse:
    outcome: Outcome | None
    """None nghĩa là 2xx và không thấy dấu hiệu chặn — caller tự quyết OK/EMPTY/PARSE_FAIL."""
    status_code: int | None
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None

    def json(self) -> Any | None:
        import json as _json

        try:
            return _json.loads(self.text)
        except (ValueError, TypeError):
            return None


async def _pace(host: str, min_delay_ms: int, jitter_ms: int) -> None:
    """Giữ khoảng cách tối thiểu giữa hai request tới cùng host."""
    lock = _host_locks.setdefault(host, asyncio.Lock())
    async with lock:
        delay = (min_delay_ms + random.uniform(0, jitter_ms)) / 1000
        elapsed = time.monotonic() - _last_hit.get(host, 0.0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        _last_hit[host] = time.monotonic()


async def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    data: Any = None,
    auth: tuple[str, str] | None = None,
    timeout_s: float | None = None,
    min_delay_ms: int | None = None,
    follow_redirects: bool = False,
) -> FetchResponse:
    """Gọi một request, trả về đã phân loại sẵn.

    `follow_redirects=False` là cố ý: với Shopify, redirect sang `/password` chính là
    tín hiệu store đóng — theo redirect sẽ biến nó thành 200 và mất thông tin đó.
    """
    host = httpx.URL(url).host or url
    await _pace(
        host,
        min_delay_ms if min_delay_ms is not None else settings.crawl_min_delay_ms,
        settings.crawl_jitter_ms,
    )

    merged = {**BROWSER_HEADERS, "User-Agent": settings.crawl_user_agent or DEFAULT_UA}
    merged.update(headers or {})

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s or settings.crawl_timeout_s,
            follow_redirects=follow_redirects,
        ) as client:
            resp = await client.request(
                method, url, headers=merged, params=params, data=data, auth=auth
            )
    except httpx.TimeoutException as exc:
        return FetchResponse(
            Outcome.UPSTREAM_ERROR, None, "", latency_ms=_ms(start), error=f"timeout: {exc}"
        )
    except httpx.RequestError as exc:
        return FetchResponse(
            Outcome.UPSTREAM_ERROR, None, "", latency_ms=_ms(start), error=f"network: {exc}"
        )

    body = resp.text

    # Redirect sang trang mật khẩu/đăng nhập = bị chặn, không phải điều hướng bình thường.
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location", "")
        outcome = (
            Outcome.BLOCKED
            if any(k in location.lower() for k in ("/password", "/login", "/challenge"))
            else Outcome.NOT_AVAILABLE
        )
        return FetchResponse(
            outcome, resp.status_code, body, dict(resp.headers), _ms(start),
            error=f"redirect → {location}",
        )

    return FetchResponse(
        classify_status(resp.status_code, body),
        resp.status_code,
        body,
        dict(resp.headers),
        _ms(start),
    )


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def retry_after_seconds(headers: dict[str, str], default: int = 30) -> int:
    try:
        return max(1, int(float(headers.get("retry-after", default))))
    except (TypeError, ValueError):
        return default
