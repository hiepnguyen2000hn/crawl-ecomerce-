import asyncio
import time
from typing import Any

import httpx

from app.config import settings

APIFY_BASE_URL = "https://api.apify.com/v2"
ACTOR_ID = "igolaizoa~facebook-ad-library-scraper"
POLL_INTERVAL = 3   # seconds between status checks
TIMEOUT = 300       # max wait seconds for actor run


class ApifyError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ApifyClient:
    def __init__(self):
        self._headers = {"Authorization": f"Bearer {settings.apify_token}"}

    async def run_actor(self, input_data: dict[str, Any]) -> tuple[list[dict], int]:
        """
        Start actor run, poll until finished, return (items, latency_ms).
        Raises ApifyError on failure.
        """
        if not settings.apify_token:
            raise ApifyError("APIFY_TOKEN not configured")

        start = time.monotonic()

        async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
            # Start the run
            run_resp = await client.post(
                f"{APIFY_BASE_URL}/acts/{ACTOR_ID}/runs",
                json=input_data,
            )
            if run_resp.status_code not in (200, 201):
                raise ApifyError(
                    f"Failed to start actor: {run_resp.text}",
                    status_code=run_resp.status_code,
                )
            run_data = run_resp.json().get("data", {})
            run_id = run_data.get("id")
            dataset_id = run_data.get("defaultDatasetId")

            if not run_id:
                raise ApifyError("No run ID returned from Apify")

            # Poll until run finishes
            elapsed = 0
            while elapsed < TIMEOUT:
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL

                status_resp = await client.get(f"{APIFY_BASE_URL}/actor-runs/{run_id}")
                if status_resp.status_code != 200:
                    continue

                run_status = status_resp.json().get("data", {}).get("status", "")

                if run_status == "SUCCEEDED":
                    break
                if run_status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    raise ApifyError(f"Actor run {run_status}: run_id={run_id}")

            else:
                raise ApifyError(f"Actor run timed out after {TIMEOUT}s")

            # Fetch dataset items
            items_resp = await client.get(
                f"{APIFY_BASE_URL}/datasets/{dataset_id}/items",
                params={"format": "json", "clean": "true"},
            )
            if items_resp.status_code != 200:
                raise ApifyError(
                    f"Failed to fetch results: {items_resp.text}",
                    status_code=items_resp.status_code,
                )

            items = items_resp.json()
            latency_ms = int((time.monotonic() - start) * 1000)
            return items, latency_ms


apify_client = ApifyClient()
