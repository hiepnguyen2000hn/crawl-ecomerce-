import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.providers.key_pool import key_pool
from app.providers.proxy_pool import proxy_pool

SERPAPI_BASE_URL = "https://serpapi.com/search"
MAX_RETRIES = 3


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProviderManager:
    """
    Orchestrates SerpAPI calls with automatic key + proxy rotation.

    Retry logic:
    - 429 → mark key on cooldown, rotate key, retry
    - Network error → mark proxy failed, rotate proxy, retry
    - 5xx → rotate both key and proxy, retry
    - Other errors → raise immediately
    """

    async def search(
        self,
        params: dict[str, Any],
        db: AsyncSession,
    ) -> tuple[dict[str, Any], int]:
        """Returns (response_json, latency_ms)."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            key_entry = await key_pool.get_available(db)
            proxy_entry = await proxy_pool.get_available(db)

            # Fallback to default key from env if no keys in DB
            api_key = key_entry.api_key if key_entry else settings.serpapi_key

            if not api_key:
                raise ProviderError("No SerpAPI key available")

            proxy_url = proxy_entry.url if proxy_entry else None
            transport = httpx.AsyncHTTPTransport(proxy=proxy_url) if proxy_url else None

            async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
                payload = {**params, "api_key": api_key}
                start = time.monotonic()

                try:
                    response = await client.get(SERPAPI_BASE_URL, params=payload)
                    latency_ms = int((time.monotonic() - start) * 1000)

                    if response.status_code == 429:
                        if key_entry:
                            await key_pool.mark_rate_limited(db, key_entry.id)
                        last_error = ProviderError("Rate limited (429)", status_code=429)
                        continue  # retry with next key

                    if response.status_code >= 500:
                        if key_entry:
                            await key_pool.mark_error(db, key_entry.id)
                        if proxy_entry:
                            await proxy_pool.mark_failed(db, proxy_entry.id)
                        last_error = ProviderError(
                            f"Server error {response.status_code}", status_code=response.status_code
                        )
                        continue  # retry

                    if response.status_code != 200:
                        raise ProviderError(
                            f"SerpAPI error {response.status_code}: {response.text}",
                            status_code=response.status_code,
                        )

                    data = response.json()
                    if "error" in data:
                        raise ProviderError(data["error"], status_code=response.status_code)

                    # Success — update counters
                    if key_entry:
                        await key_pool.mark_used(db, key_entry.id)
                    if proxy_entry:
                        await proxy_pool.mark_used(db, proxy_entry.id)

                    return data, latency_ms

                except httpx.RequestError as exc:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    if proxy_entry:
                        await proxy_pool.mark_failed(db, proxy_entry.id)
                    last_error = ProviderError(f"Network error: {exc}")
                    continue  # retry with next proxy

        raise ProviderError(
            f"All {MAX_RETRIES} attempts failed. Last error: {last_error}",
            status_code=getattr(last_error, "status_code", None),
        )


provider_manager = ProviderManager()
