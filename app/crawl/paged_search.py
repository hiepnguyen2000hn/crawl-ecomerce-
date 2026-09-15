"""Vòng lặp "tìm kiếm nhiều trang bằng trình duyệt", dùng chung cho mọi nguồn T2.

Ba nguồn tự scrape (Amazon · 1688 · Taobao) khác nhau ở URL và ở cách đọc HTML, nhưng
giống hệt nhau ở phần khó: khi nào dừng, khi nào là `EMPTY`, khi nào là `PARSE_FAIL`,
và giữ lại bao nhiêu khi gãy giữa chừng. Đó là phần dễ viết sai nhất — nên nó nằm ở
một chỗ duy nhất thay vì được chép ba lần.

Ba quy tắc được cưỡng chế ở đây:

  1. **Trang đầu không ra gì thì phải phân định EMPTY hay PARSE_FAIL.** Nhầm hai cái
     này rất tốn: một bên là câu trả lời hợp lệ, một bên là parser đã hỏng mà không ai
     biết — dữ liệu rỗng cứ thế trôi sang AI.
  2. **Gãy giữa chừng vẫn giữ phần đã lấy được.** Đã đọc xong 2/3 trang rồi mới bị
     chặn thì kết quả là OK với 2 trang, không phải vứt cả lần crawl.
  3. **Hết sản phẩm thì dừng, không chạy cho đủ `max_pages`.** Mỗi trang thừa là một
     lần mở trang thật, tốn thời gian và tăng nguy cơ bị chặn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.crawl import browser_fetch
from app.crawl.outcomes import Outcome

#: Hàm dựng URL cho trang thứ N (đánh số từ 1).
UrlFor = Callable[[int], str]
#: HTML → (danh sách bản ghi đã chuẩn hoá, tên nhánh parser đã dùng).
Parse = Callable[[str], "tuple[list[dict], str | None]"]
#: HTML → có phải trang "không có kết quả" thật không.
LooksEmpty = Callable[[str], bool]


@dataclass
class PagedResult:
    outcome: Outcome
    items: list[dict] = field(default_factory=list)
    pages_fetched: int = 0
    parse_source: str | None = None
    latency_ms: int = 0
    error: str | None = None
    fingerprint_seed: int | None = None


async def run(
    *,
    source: str,
    max_pages: int,
    url_for: UrlFor,
    parse: Parse,
    looks_empty: LooksEmpty,
    wait_for: str | None = None,
    settle_ms: int = browser_fetch.DEFAULT_SETTLE_MS,
    parse_fail_hint: str = "",
) -> PagedResult:
    """Duyệt trang 1..max_pages, dừng sớm khi hết dữ liệu hoặc gặp sự cố."""
    result = PagedResult(outcome=Outcome.OK)

    for page in range(1, max_pages + 1):
        resp = await browser_fetch.fetch_page(
            url_for(page), source=source, wait_for=wait_for, settle_ms=settle_ms
        )
        result.latency_ms += resp.latency_ms
        result.fingerprint_seed = resp.fingerprint_seed

        if resp.outcome is not None:
            result.outcome = resp.outcome
            result.error = resp.error
            break

        items, parse_source = parse(resp.text)
        result.parse_source = parse_source or result.parse_source

        if not items:
            if page == 1:
                if looks_empty(resp.text):
                    result.outcome = Outcome.EMPTY
                else:
                    result.outcome = Outcome.PARSE_FAIL
                    result.error = (
                        "200, không thấy dấu hiệu bị chặn, nhưng không đọc ra bản ghi nào "
                        f"— nhiều khả năng {source} đã đổi cấu trúc trang. {parse_fail_hint}"
                    ).strip()
            break

        result.items.extend(items)
        result.pages_fetched = page

    # Quy tắc 2: có dữ liệu thì lần crawl này là thành công, dù trang cuối có gãy.
    if result.items:
        result.outcome = Outcome.OK
        result.error = None

    return result
