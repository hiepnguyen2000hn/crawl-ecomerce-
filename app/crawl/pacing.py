"""Giới hạn đồng thời + nhịp tối thiểu theo nguồn, dùng trong `engine.py`.

⚠️ Cũng như `app/crawl/http.py`, khoá ở đây là **khoá trong process**. Đúng khi chạy
một worker; sai khi scale nhiều container — 1.1 trong docs/DEV-Design-Crawl-Engine.md
đã chỉ rõ lỗi này. Nâng lên semaphore/nhịp phân tán qua Redis là việc của G2
(`app/crawl/identity.py`), không phải phạm vi G1. Interface (`acquire_slot`) giữ
nguyên tên/chữ ký để khi nâng cấp không phải sửa `engine.py`.
"""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager

_semaphores: dict[str, asyncio.Semaphore] = {}
_last_hit: dict[str, float] = {}
_paced_locks: dict[str, asyncio.Lock] = {}


def _semaphore(source: str, max_concurrency: int) -> asyncio.Semaphore:
    sem = _semaphores.get(source)
    if sem is None:
        sem = asyncio.Semaphore(max_concurrency)
        _semaphores[source] = sem
    return sem


async def _pace(source: str, min_delay_ms: int, jitter_ms: int) -> None:
    lock = _paced_locks.setdefault(source, asyncio.Lock())
    async with lock:
        delay = (min_delay_ms + random.uniform(0, jitter_ms)) / 1000
        elapsed = time.monotonic() - _last_hit.get(source, 0.0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        _last_hit[source] = time.monotonic()


@asynccontextmanager
async def acquire_slot(source: str, max_concurrency: int, min_delay_ms: int, jitter_ms: int):
    """Giữ tối đa `max_concurrency` request đồng thời cho `source`, cách nhau tối
    thiểu `min_delay_ms` (+ jitter ngẫu nhiên — nhịp đều tăm tắp là tín hiệu bot)."""
    sem = _semaphore(source, max_concurrency)
    async with sem:
        await _pace(source, min_delay_ms, jitter_ms)
        yield
