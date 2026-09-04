import time
from typing import Any

import httpx

from app.config import settings

SERPAPI_BASE_URL = "https://serpapi.com/search"


class SerpApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SerpApiClient:
    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0)

    async def search(self, params: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """
        Call SerpAPI and return (response_json, latency_ms).
        Raises SerpApiError on non-2xx or API-level errors.
        """
        payload = {**params, "api_key": settings.serpapi_key}

        start = time.monotonic()
        try:
            response = await self._client.get(SERPAPI_BASE_URL, params=payload)
            latency_ms = int((time.monotonic() - start) * 1000)

            if response.status_code != 200:
                raise SerpApiError(
                    f"SerpAPI returned {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )

            data = response.json()

            if "error" in data:
                raise SerpApiError(data["error"], status_code=response.status_code)

            return data, latency_ms

        except httpx.RequestError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            raise SerpApiError(f"Network error: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


serpapi_client = SerpApiClient()
