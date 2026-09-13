"""Trần chi phí cho các nhà cung cấp tính tiền theo lượt chạy.

VÌ SAO CÓ FILE NÀY

Một actor Apify được gọi với `maxItems: 10` đã trả về 2.693 item và tốn **$3.98**
trong một lần chạy — gần hết hạn mức tháng. `maxItems` với nhiều actor chỉ là *gợi ý*,
không phải ràng buộc; tin vào nó là tin vào lời hứa của bên thứ ba.

Thứ duy nhất chặn được thật là **theo dõi chi phí trong lúc chạy và hủy khi vượt trần**.
Kiểm tra trước khi chạy là chưa đủ: một run có thể đi từ $0 lên $4 trong vài phút.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"


class BudgetExceeded(Exception):
    """Ném ra khi một lần chạy chạm trần và đã bị hủy."""

    def __init__(self, spent: float, cap: float):
        super().__init__(f"Chi phí ${spent:.4f} vượt trần ${cap:.2f} — đã hủy run")
        self.spent, self.cap = spent, cap


async def remaining_apify_credit(client: httpx.AsyncClient) -> float | None:
    """Credit còn lại trong tháng. None nếu không đọc được (không chặn vì lý do đó)."""
    try:
        r = await client.get(f"{APIFY_BASE}/users/me/limits")
        if r.status_code != 200:
            return None
        d = r.json()["data"]
        used = float(d.get("current", {}).get("monthlyUsageUsd") or 0)
        cap = float(d.get("limits", {}).get("maxMonthlyUsageUsd") or 0)
        return max(0.0, cap - used)
    except (httpx.RequestError, KeyError, ValueError, TypeError):
        return None


async def guard_before_run(client: httpx.AsyncClient) -> str | None:
    """Kiểm tra trước khi khởi động. Trả thông báo lỗi nếu không nên chạy."""
    cap = settings.apify_max_cost_per_run_usd
    left = await remaining_apify_credit(client)
    if left is None:
        return None  # không đọc được hạn mức → để trần theo run lo phần còn lại
    if left < cap:
        return (
            f"Credit Apify còn ${left:.2f}, thấp hơn trần một lần chạy ${cap:.2f}. "
            "Nạp thêm hoặc hạ APIFY_MAX_COST_PER_RUN_USD."
        )
    return None


async def abort_run(client: httpx.AsyncClient, run_id: str) -> None:
    try:
        await client.post(f"{APIFY_BASE}/actor-runs/{run_id}/abort")
        logger.error("Đã hủy Apify run %s vì chạm trần chi phí", run_id)
    except httpx.RequestError as exc:
        logger.error("Không hủy được run %s: %s", run_id, exc)


def over_cap(spent: float) -> bool:
    return spent > settings.apify_max_cost_per_run_usd
