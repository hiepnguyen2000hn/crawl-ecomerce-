"""Cache theo `idempotency_key` trên Redis — xem docs §9.3.

AI Service được phép hỏi lại (retry) mà không tốn credit vendor: trong `cache_ttl`,
cùng request trả lại đúng kết quả cũ với `cost_usd = 0`. `cache_ttl` nằm trong
policy (`crawl_source_policies.cache_ttl_seconds`), khác nhau theo nguồn.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from app.crawl.contracts import FetchResult
from app.crawl.outcomes import Outcome

_PREFIX = "crawlcache:"


def _key(idempotency_key: str) -> str:
    return f"{_PREFIX}{idempotency_key}"


async def cache_lookup(redis: Any, idempotency_key: str) -> FetchResult | None:
    if redis is None:
        return None
    raw = await redis.get(_key(idempotency_key))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    data["outcome"] = Outcome(data["outcome"])
    result = FetchResult(**data)
    result.stale = True
    result.cost_usd = 0.0
    return result


async def cache_store(redis: Any, idempotency_key: str, result: FetchResult, ttl_seconds: int) -> None:
    if redis is None or ttl_seconds <= 0:
        return
    payload = dataclasses.asdict(result)
    await redis.set(_key(idempotency_key), json.dumps(payload), ex=ttl_seconds)
