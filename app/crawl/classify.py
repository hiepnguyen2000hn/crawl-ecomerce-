"""Phân loại cuối cùng sau khi một adapter đã chạy — bước 9 trong pipeline `engine.py`.

Khác với `outcomes.classify_status` (phân loại ở biên HTTP, dựa vào status code +
thân phản hồi), hàm ở đây phân loại dựa vào chính `FetchResult` mà adapter trả về —
áp dụng bất kể adapter đó gọi HTTP trực tiếp, gọi vendor API, hay đọc fixture giả.

Đặt trong `finally` của engine (§7.3): cả nhánh exception lẫn nhánh "200 kèm captcha"
đều đi qua đúng một cửa, không lọt qua `except` nào.
"""

from __future__ import annotations

from app.crawl.contracts import FetchResult
from app.crawl.outcomes import Outcome


def classify(result: FetchResult, source: str) -> Outcome:
    """Áp lưới an toàn lên outcome mà adapter tự khai.

    Một adapter viết vội có thể trả `OK` kèm `items=[]` (chưa kịp phân biệt "gọi
    thành công" với "có dữ liệu"). Bắt ở đây một lần, thay vì tin từng adapter tự
    làm đúng — false OK mà lọt lên trên coi như dữ liệu rỗng trôi vào AI mà không
    ai biết.
    """
    if result.outcome is Outcome.OK and not result.items:
        return Outcome.EMPTY
    return result.outcome
