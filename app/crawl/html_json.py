"""Bóc object JSON được nhúng thẳng vào JavaScript của trang.

Nhiều sàn không đặt dữ liệu vào HTML mà gán vào một biến toàn cục rồi để JS dựng DOM
từ đó (`window.g_page_config = {...}` của Taobao, `window.__INIT_DATA__` của 1688).
Với những trang này, **đọc biến đó ổn định hơn đọc DOM rất nhiều**: DOM là thứ họ đổi
mỗi đợt A/B test, còn cấu trúc dữ liệu thì cả backend lẫn app di động cùng phụ thuộc.

Không dùng regex `\\{.*\\}` để cắt: chuỗi trong JSON có thể chứa `{` `}` và dấu ngoặc
lồng nhau, regex tham lam sẽ cắt nhầm. Ở đây đếm độ sâu ngoặc và bỏ qua phần nằm
trong chuỗi — dài hơn vài dòng nhưng đúng.
"""

from __future__ import annotations

import json
from typing import Any


def extract_js_object(html: str, *var_names: str) -> Any | None:
    """Tìm `<var_name> = { ... }` trong `html`, trả object đã parse.

    Thử lần lượt từng tên cho tới khi được — các sàn đổi tên biến theo phiên bản, và
    biết tên nào còn sống là việc của người vận hành chứ không nên là một lần deploy.
    Trả None nếu không tên nào khớp hoặc phần cắt ra không phải JSON hợp lệ.
    """
    for name in var_names:
        start = html.find(name)
        while start != -1:
            brace = html.find("{", start + len(name))
            if brace == -1:
                break
            # Giữa tên biến và `{` chỉ được phép có dấu `=`/khoảng trắng. Nếu có thứ
            # khác thì `name` đang nằm trong một đoạn văn bản nào đó, không phải phép gán.
            between = html[start + len(name) : brace]
            if between.strip(" \t\r\n=:") == "":
                raw = _balanced_slice(html, brace)
                if raw:
                    try:
                        return json.loads(raw)
                    except ValueError:
                        pass
            start = html.find(name, start + 1)
    return None


def _balanced_slice(text: str, open_idx: int) -> str | None:
    """Cắt từ `{` tới `}` cân bằng tương ứng, bỏ qua ngoặc nằm trong chuỗi."""
    depth = 0
    in_string = False
    quote = ""
    escaped = False

    for i in range(open_idx, len(text)):
        ch = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_string = False
            continue

        if ch in ('"', "'"):
            in_string = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx : i + 1]

    return None


def walk(node: Any, key: str) -> list[Any]:
    """Gom mọi giá trị của `key` ở mọi độ sâu.

    Cấu trúc JSON nhúng của các sàn rất sâu và đổi chỗ giữa các phiên bản; bám đường
    dẫn tuyệt đối (`data.result.items`) là vỡ ngay lần đầu họ bọc thêm một lớp. Tìm
    theo tên khoá thì chịu được việc đó.
    """
    found: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(walk(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(walk(item, key))
    return found
