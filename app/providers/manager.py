import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.account_checker import check_account
from app.providers.google_trends_direct import GoogleBlockedError, google_trends_direct
from app.providers.key_pool import serpapi_key_pool
from app.providers.proxy_pool import proxy_pool

SERPAPI_BASE_URL = "https://serpapi.com/search"
MAX_KEY_RETRIES = 3
MAX_PROXY_RETRIES = 3


class AllKeysExhausted(Exception):
    pass


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProviderManager:
    """
    Fallback cascade:
    1. SerpAPI (rotate keys from env on 429)
    2. Direct Google Trends (pytrends, no proxy)
    3. Direct Google + proxy rotation (proxy still from DB)
    """

    async def search(self, params: dict[str, Any], db: AsyncSession) -> tuple[dict[str, Any], int]:
        try:
            return await self._try_serpapi(params)
        except AllKeysExhausted:
            pass

        try:
            return await self._try_direct_google(params, proxy_url=None)
        except GoogleBlockedError:
            pass

        return await self._try_direct_with_proxies(params, db)

    async def _try_serpapi(self, params: dict[str, Any]) -> tuple[dict[str, Any], int]:
        tried: set[str] = set()

        for _ in range(MAX_KEY_RETRIES):
            api_key = serpapi_key_pool.get_available()
            if api_key is None or api_key in tried:
                raise AllKeysExhausted("All SerpAPI keys exhausted or rate-limited")
            tried.add(api_key)

            account = await check_account(api_key)
            if account.is_exhausted:
                serpapi_key_pool.mark_rate_limited(api_key)
                continue

            try:
                data, latency_ms = await self._call_serpapi(api_key, params)
                serpapi_key_pool.mark_used(api_key)
                return data, latency_ms
            except ProviderError as exc:
                if exc.status_code == 429 or "run out" in str(exc).lower():
                    serpapi_key_pool.mark_rate_limited(api_key)
                    continue
                raise

        raise AllKeysExhausted(f"All {len(tried)} SerpAPI key(s) failed")

    async def _call_serpapi(self, api_key: str, params: dict[str, Any]) -> tuple[dict[str, Any], int]:
        payload = {**params, "api_key": api_key}
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(SERPAPI_BASE_URL, params=payload)
                latency_ms = int((time.monotonic() - start) * 1000)
                if response.status_code == 429:
                    raise ProviderError("Rate limited (429)", status_code=429)
                if response.status_code != 200:
                    raise ProviderError(f"SerpAPI {response.status_code}: {response.text}", status_code=response.status_code)
                data = response.json()
                if "error" in data:
                    raise ProviderError(data["error"], status_code=response.status_code)
                return data, latency_ms
        except httpx.RequestError as exc:
            raise ProviderError(f"Network error: {exc}") from exc

    async def _try_direct_google(self, params: dict[str, Any], proxy_url: str | None) -> tuple[dict[str, Any], int]:
        start = time.monotonic()
        data_type = params.get("data_type", "TIMESERIES")
        date = params.get("date", "today 12-m")
        geo = params.get("geo", "")

        if data_type == "TIMESERIES":
            keywords = [k.strip() for k in params.get("q", "").split(",")]
            raw = await google_trends_direct.fetch_interest_over_time(
                keywords=keywords, date=date, geo=geo,
                cat=int(params.get("cat", 0)), gprop=params.get("gprop", ""),
                proxy_url=proxy_url,
            )
        else:
            raw = await google_trends_direct.fetch_related_queries(
                keyword=params.get("q", ""), date=date, geo=geo, proxy_url=proxy_url,
            )

        return raw, int((time.monotonic() - start) * 1000)

    async def _try_direct_with_proxies(self, params: dict[str, Any], db: AsyncSession) -> tuple[dict[str, Any], int]:
        tried: set[int] = set()
        for _ in range(MAX_PROXY_RETRIES):
            proxy_entry = await proxy_pool.get_available(db)
            if proxy_entry is None:
                raise ProviderError("All SerpAPI keys exhausted and Google blocked the IP. Add proxies via /api/v1/providers/proxies.")
            if proxy_entry.id in tried:
                break
            tried.add(proxy_entry.id)
            try:
                data, latency_ms = await self._try_direct_google(params, proxy_url=proxy_entry.url)
                await proxy_pool.mark_used(db, proxy_entry.id)
                return data, latency_ms
            except GoogleBlockedError:
                await proxy_pool.mark_failed(db, proxy_entry.id)
        raise ProviderError("All SerpAPI keys exhausted and all proxies blocked by Google.")


provider_manager = ProviderManager()
