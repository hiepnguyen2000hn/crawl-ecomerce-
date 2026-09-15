"""Lấy HTML bằng trình duyệt thật (CloakBrowser) — tầng T2.

Song song với `app/crawl/http.py`: cùng một vai trò, cùng hình dạng trả về, khác mỗi
cách đi. Nhờ vậy adapter T2 viết giống hệt adapter dùng HTTP thường và **không biết
gì về Playwright / CDP / fingerprint** — đổi hạ tầng trình duyệt sau này chỉ sửa ở đây.

Dùng khi nào: nguồn trả HTML rỗng vì nội dung do JS dựng (1688, Taobao), hoặc chặn
thẳng HTTP client dù đã đủ header (Amazon). Nguồn nào `http.fetch` còn ăn thì **đừng**
dùng file này — mở một Chrome tốn ~190MB RAM và chậm hơn vài chục lần.

Giới hạn ở G1 — đọc trước khi tin vào kết quả:
  - Chưa có identity pool (`app/crawl/identity.py` thuộc G2), nên fingerprint seed suy
    ra từ tên nguồn: cùng một nguồn luôn dùng cùng seed qua mọi lần chạy. Đó là hành vi
    ĐÚNG (một "máy" dùng nhiều lần), nhưng chỉ có MỘT máy cho mỗi nguồn — chưa xoay vòng.
  - Chưa gắn proxy. Amazon cần residential khớp marketplace, 1688/Taobao cần residential
    Trung Quốc đại lục (docs §6). Thiếu proxy thì hai nguồn Alibaba gần như chắc chắn
    trả BLOCKED — đó là kết quả có thật cần được ghi lại, không phải lý do để bỏ qua.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field

from app.config import settings
from app.crawl.outcomes import Outcome, classify_status

logger = logging.getLogger(__name__)

#: Chờ thêm sau khi DOM sẵn sàng, cho JS kịp dựng danh sách sản phẩm. Các sàn TQ
#: render phía client nên `domcontentloaded` bắn ra lúc trang vẫn còn trống.
DEFAULT_SETTLE_MS = 2500

DEFAULT_TIMEOUT_MS = 45_000


@dataclass
class BrowserResponse:
    """Cùng hình dạng với `http.FetchResponse` — xem docstring đầu file."""

    outcome: Outcome | None
    """None nghĩa là tải được và không thấy dấu hiệu chặn — caller tự quyết
    OK / EMPTY / PARSE_FAIL dựa trên thứ parse được."""
    status_code: int | None
    text: str
    """HTML **sau khi JS chạy** (`page.content()`), không phải phản hồi thô."""
    latency_ms: int = 0
    error: str | None = None
    fingerprint_seed: int | None = None
    """Trả ngược ra để ghi vào `crawl_attempts` — khi một seed bắt đầu bị chặn liên
    tục thì phải thấy được nó là seed nào."""
    final_url: str = ""
    """URL sau mọi redirect. 1688/Taobao đẩy sang `/punish` hoặc `login.taobao.com`
    mà vẫn trả 200, nên URL cuối mới là bằng chứng bị chặn, không phải status code."""


def seed_for(source: str) -> int:
    """Fingerprint seed ổn định theo nguồn.

    Phải ổn định: cùng seed = cùng canvas/WebGL/font qua mọi lần mở, tức là anti-bot
    thấy "một máy quen quay lại". Sinh seed ngẫu nhiên mỗi lần mở là tự khai báo bot —
    không máy thật nào đổi phần cứng sau mỗi lần truy cập.

    G2 sẽ thay bằng seed của identity được cấp phát; giữ hàm này làm chỗ nối.
    """
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) % 999_999_999 + 1


async def fetch_page(
    url: str,
    *,
    source: str,
    wait_for: str | None = None,
    settle_ms: int = DEFAULT_SETTLE_MS,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    proxy_url: str | None = None,
    fingerprint_seed: int | None = None,
) -> BrowserResponse:
    """Mở `url` bằng CloakBrowser, trả HTML đã render kèm outcome đã phân loại.

    `wait_for` là CSS selector của thứ CHỨNG MINH trang đã có dữ liệu (ví dụ thẻ sản
    phẩm đầu tiên). Chờ đúng selector thay vì ngủ cố định giúp phân biệt được hai
    trường hợp mà `sleep()` gộp làm một: trang render chậm, và trang đã render xong
    nhưng không có sản phẩm nào.
    """
    seed = fingerprint_seed if fingerprint_seed is not None else seed_for(source)
    start = time.monotonic()

    # Import trong hàm: playwright/cloakbrowser chỉ cần khi thật sự chạy T2. Import ở
    # đầu module sẽ làm cả API lẫn worker không khởi động nổi trên môi trường chỉ chạy
    # các nguồn T0 (Shopify) — nơi không ai cài trình duyệt.
    try:
        from app.services.cloak_browser import browser_pool
    except ImportError as exc:
        return BrowserResponse(
            Outcome.NOT_AVAILABLE,
            None,
            "",
            _ms(start),
            error=f"Chưa cài playwright/cloakbrowser: {exc}",
            fingerprint_seed=seed,
        )

    try:
        async with browser_pool.acquire(
            fingerprint_seed=seed, proxy_url=proxy_url, headless=True
        ) as session:
            page = session.page
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            status = response.status if response is not None else None

            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=timeout_ms // 3)
                except Exception:
                    # Không thấy selector KHÔNG phải lỗi ở đây: có thể trang chặn, có
                    # thể 0 kết quả thật. Cứ lấy HTML về rồi để parser phân định —
                    # ném lỗi tại đây sẽ xoá mất chính bằng chứng cần để phân biệt.
                    logger.info("Không thấy selector %r trên %s — vẫn lấy HTML về", wait_for, url)

            if settle_ms > 0:
                await asyncio.sleep(settle_ms / 1000)

            html = await page.content()
            final_url = page.url
    except Exception as exc:  # noqa: BLE001 — biên trình duyệt, lỗi gì cũng phải phân loại được
        return BrowserResponse(
            Outcome.UPSTREAM_ERROR,
            None,
            "",
            _ms(start),
            error=f"{type(exc).__name__}: {exc}",
            fingerprint_seed=seed,
        )

    outcome = classify_status(status or 200, html)

    # Trang chặn của Alibaba trả 200 và HTML hợp lệ, chỉ URL là đổi. Bắt ở đây vì
    # `classify_status` chỉ nhìn được thân phản hồi.
    if outcome is None and _redirected_to_block(final_url):
        outcome = Outcome.BLOCKED

    return BrowserResponse(
        outcome,
        status,
        html,
        _ms(start),
        error=None if outcome is None else f"{outcome} tại {final_url}",
        fingerprint_seed=seed,
        final_url=final_url,
    )


#: Dấu hiệu bị đẩy sang trang chặn, nhận qua URL cuối cùng.
_BLOCK_URL_MARKERS = (
    "/punish",          # 1688 / Taobao
    "login.taobao.com",
    "x5secdata",
    "/errors/validatecaptcha",  # Amazon
    "/ap/signin",
)


def _redirected_to_block(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in _BLOCK_URL_MARKERS)


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
