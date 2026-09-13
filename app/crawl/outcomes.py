"""Phân loại kết quả một lần gọi nguồn.

Đây là điểm khác biệt chính so với cách `try/except ApifyError` hiện có: gói mọi thứ
vào "error" rồi retry đều nhau vừa vô ích (captcha retry bao nhiêu cũng captcha) vừa
có hại (đốt thêm identity / quota). Mỗi outcome dưới đây ứng với một hành động khác nhau.

Xem docs/DEV-Design-Crawl-Engine.md §4.
"""

from __future__ import annotations

from enum import StrEnum


class Outcome(StrEnum):
    OK = "OK"
    """Parse ra dữ liệu hợp lệ."""

    EMPTY = "EMPTY"
    """Phản hồi hợp lệ, 0 kết quả. KHÔNG retry — đây là một câu trả lời, không phải lỗi."""

    RATE_LIMITED = "RATE_LIMITED"
    """429 / 430 / Retry-After. Chờ rồi thử lại — cùng identity."""

    BLOCKED = "BLOCKED"
    """Captcha, challenge, 403, store đặt mật khẩu. Phải đổi identity mới thử lại."""

    NOT_AVAILABLE = "NOT_AVAILABLE"
    """404, endpoint bị tắt, domain không phải nền tảng ta tưởng. KHÔNG retry."""

    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    """5xx, timeout, reset. Retry cùng identity, backoff mũ."""

    PARSE_FAIL = "PARSE_FAIL"
    """200, không có dấu hiệu bị chặn, nhưng không đọc ra dữ liệu.

    Nghĩa là nguồn đã đổi cấu trúc. Retry vô ích — phải BÁO ĐỘNG cho dev.
    Đây là loại hầu hết crawler bỏ sót và là lý do dữ liệu rỗng trôi âm thầm sang AI.
    """

    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    """Hết credit/quota của tầng hiện tại. Nhảy sang tầng kế tiếp."""


#: Outcome cho phép thử lại, và thử lại có ý nghĩa gì.
RETRY_SAME_IDENTITY = frozenset({Outcome.RATE_LIMITED, Outcome.UPSTREAM_ERROR})
RETRY_NEW_IDENTITY = frozenset({Outcome.BLOCKED})
NO_RETRY = frozenset(
    {Outcome.OK, Outcome.EMPTY, Outcome.NOT_AVAILABLE, Outcome.PARSE_FAIL, Outcome.QUOTA_EXHAUSTED}
)

#: Outcome cần đánh động cho người trực, không chỉ ghi log.
ALERT_WORTHY = frozenset({Outcome.PARSE_FAIL})


def should_retry(outcome: Outcome) -> bool:
    return outcome in RETRY_SAME_IDENTITY or outcome in RETRY_NEW_IDENTITY


def is_failure(outcome: Outcome) -> bool:
    """EMPTY không phải thất bại — nguồn đã trả lời, chỉ là không có gì."""
    return outcome not in (Outcome.OK, Outcome.EMPTY)


# ── Dấu hiệu nhận biết theo nền tảng ─────────────────────────────────────────
# Kiểm tra BODY chứ không chỉ status code: nhiều nền tảng trả 200 kèm trang chặn.

CLOUDFLARE_MARKERS = (
    "__cf_chl",
    "cf-mitigated",
    "Just a moment",
    "Attention Required! | Cloudflare",
)

CAPTCHA_MARKERS = (
    "/errors/validateCaptcha",           # Amazon
    "Enter the characters you see below",  # Amazon
    "captcha",
    "recaptcha",
)


def looks_blocked(body: str) -> bool:
    """Tìm dấu hiệu bị chặn trong thân phản hồi (đã 200 nhưng không phải dữ liệu)."""
    sample = body[:4000].lower()
    return any(m.lower() in sample for m in CLOUDFLARE_MARKERS + CAPTCHA_MARKERS)


def classify_status(status_code: int, body: str = "") -> Outcome | None:
    """Phân loại theo status code + body. Trả None nếu 2xx và không thấy dấu hiệu chặn
    (khi đó caller tự quyết OK / EMPTY / PARSE_FAIL dựa trên nội dung parse được)."""
    if status_code in (429, 430):
        return Outcome.RATE_LIMITED
    if status_code in (401, 403):
        return Outcome.BLOCKED
    if status_code == 404:
        return Outcome.NOT_AVAILABLE
    if status_code >= 500:
        return Outcome.UPSTREAM_ERROR
    if 200 <= status_code < 300:
        return Outcome.BLOCKED if looks_blocked(body) else None
    return Outcome.UPSTREAM_ERROR
