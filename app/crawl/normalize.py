"""Chuẩn hoá giá về integer minor unit.

Quy ước chung với levelup_be: **không bao giờ lưu giá dạng float**. 19.99 EUR lưu là
(1999, "EUR"). Float làm sai lệch khi cộng dồn và khi so với ngưỡng COGS_max ở Bước 3.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

#: Tiền tệ không có phần lẻ — nhân 10^0 chứ không phải 10^2.
ZERO_DECIMAL = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "HUF", "TWD", "XAF", "XOF"})

_NUM_RE = re.compile(r"[-+]?[\d][\d.,\s ]*")


def exponent_for(currency: str | None) -> int:
    if currency and currency.upper() in ZERO_DECIMAL:
        return 0
    return 2


def to_minor(amount: Decimal | str | float, currency: str | None) -> int | None:
    """Đổi số tiền sang minor unit. Trả None nếu không đọc được."""
    if isinstance(amount, str):
        dec = parse_decimal(amount)
    elif isinstance(amount, float):
        dec = Decimal(str(amount))
    else:
        dec = amount
    if dec is None:
        return None
    return int((dec * (10 ** exponent_for(currency))).to_integral_value())


def parse_decimal(text: str | None) -> Decimal | None:
    """Đọc số tiền từ chuỗi, chịu được cả định dạng EU lẫn US.

    Thị trường target là châu Âu nên đây không phải chi tiết vặt:
        "1.299,00"  (DE/NL/IT/ES)  → 1299.00
        "1,299.00"  (US/UK)        → 1299.00
        "29,99"                    → 29.99
        "1.299"                    → 1299     (dấu chấm ngăn nghìn, không phải thập phân)
    """
    if not text:
        return None
    m = _NUM_RE.search(str(text))
    if not m:
        return None
    raw = m.group(0).strip().replace(" ", "").replace(" ", "")

    last_dot, last_comma = raw.rfind("."), raw.rfind(",")

    if last_dot >= 0 and last_comma >= 0:
        # Cả hai cùng xuất hiện → cái đứng SAU là dấu thập phân.
        if last_comma > last_dot:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif last_comma >= 0:
        # Chỉ có dấu phẩy: 2 chữ số đằng sau → thập phân; ngược lại là ngăn nghìn.
        raw = raw.replace(",", "." if len(raw) - last_comma - 1 == 2 else "")
    elif last_dot >= 0:
        # Chỉ có dấu chấm: 3 chữ số đằng sau và có nhiều nhóm → ngăn nghìn ("1.299").
        if len(raw) - last_dot - 1 == 3:
            raw = raw.replace(".", "")

    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def normalize_domain(value: str) -> str:
    """`https://shop.com/products/abc?x=1` → `shop.com`. Bỏ `www.` cho khớp khoá tự nhiên."""
    v = str(value).strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = v.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if v.startswith("www."):
        v = v[4:]
    return v
