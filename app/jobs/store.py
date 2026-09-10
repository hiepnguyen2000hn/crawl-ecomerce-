"""JobStore — job-status store trên Redis, dùng chung cho mọi loại crawl job.

Status vocabulary: QUEUED | RUNNING | SUCCEEDED | FAILED.
Cùng pattern với levelup_ai (app/jobs/store.py) để 2 service Python trong org
dùng chung 1 cách nghĩ về async job, dễ maintain hơn khi đổi người.
"""

import json
from typing import Any


class JobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def _key(job_id: str) -> str:
    return f"job:{job_id}"


class JobStore:
    def __init__(self, redis: Any, ttl_seconds: int = 86400):
        self._redis = redis
        self._ttl = ttl_seconds

    async def create(self, job_id: str, type: str) -> None:
        await self._save(
            {"job_id": job_id, "type": type, "status": JobStatus.QUEUED, "result": None, "error": None}
        )

    async def set_status(
        self, job_id: str, status: str, result: Any | None = None, error: Any | None = None
    ) -> None:
        data = await self.get(job_id) or {"job_id": job_id, "type": None, "result": None, "error": None}
        data["status"] = status
        if result is not None:
            data["result"] = result
        if error is not None:
            data["error"] = error
        await self._save(data)

    async def get(self, job_id: str) -> dict | None:
        raw = await self._redis.get(_key(job_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def _save(self, data: dict) -> None:
        await self._redis.set(_key(data["job_id"]), json.dumps(data), ex=self._ttl)
