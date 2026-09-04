import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.providers.account_checker import check_account
from app.providers.google_trends_direct import GoogleBlockedError, google_trends_direct
from app.providers.key_pool import key_pool
from app.providers.proxy_pool import proxy_pool

SERPAPI_BASE_URL = "https://serpapi.com/search"
MAX_KEY_RETRIES = 3   # max SerpAPI key rotations before fallback
MAX_PROXY_RETRIES = 3  # max proxy rotations when Google blocks


class AllKeysExhausted(Exception):
    """All SerpAPI keys are rate-limited or out of searches."""


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProviderManager:
    """
    Fallback cascade:

    1. SerpAPI (rotate keys on 429 / exhausted)
       ↓ all keys exhausted
    2. Direct Google Trends (pytrends, no proxy first)
       ↓ Google blocks IP (429 / captcha)
    3. Direct Google Trends + proxy rotation
    """

    async def search(
        self,
        params: dict[str, Any],
        db: AsyncSession,
    ) -> tuple[dict[str, Any], int]:
        """Returns (response_json, latency_ms)."""

        # ── Stage 1: SerpAPI with key rotation ──────────────────────────────
        try:
            return await self._try_serpapi(params, db)
        except AllKeysExhausted:
            pass  # fall through to direct Google

        # ── Stage 2: Direct Google Trends (no proxy first) ──────────────────
        try:
            return await self._try_direct_google(params, proxy_url=None)
        except GoogleBlockedError:
            pass  # IP blocked → try proxies

        # ── Stage 3: Direct Google + proxy rotation ──────────────────────────
        return await self._try_direct_with_proxies(params, db)

    # ── SerpAPI ──────────────────────────────────────────────────────────────

    async def _try_serpapi(
        self, params: dict[str, Any], db: AsyncSession
    ) -> tuple[dict[str, Any], int]:
        tried_keys: set[int] = set()

        for _ in range(MAX_KEY_RETRIES):
            key_entry = await key_pool.get_available(db)

            # No key in DB → use env fallback once
            if key_entry is None:
                api_key = settings.serpapi_key
                if not api_key:
                    raise AllKeysExhausted("No SerpAPI key configured")
                return await self._call_serpapi(api_key, params)

            if key_entry.id in tried_keys:
                break  # already tried all available keys
            tried_keys.add(key_entry.id)

            # Check remaining searches before calling
            account = await check_account(key_entry.api_key)
            if account.is_exhausted:
                await key_pool.mark_rate_limited(db, key_entry.id)
                continue

            try:
                data, latency_ms = await self._call_serpapi(key_entry.api_key, params)
                await key_pool.mark_used(db, key_entry.id)
                return data, latency_ms

            except ProviderError as exc:
                if exc.status_code == 429 or "run out" in str(exc).lower():
                    await key_pool.mark_rate_limited(db, key_entry.id)
                    continue
                await key_pool.mark_error(db, key_entry.id)
                raise

        raise AllKeysExhausted(f"All {len(tried_keys)} SerpAPI key(s) exhausted or rate-limited")

    async def _call_serpapi(
        self, api_key: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        payload = {**params, "api_key": api_key}
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(SERPAPI_BASE_URL, params=payload)
                latency_ms = int((time.monotonic() - start) * 1000)

                if response.status_code == 429:
                    raise ProviderError("Rate limited (429)", status_code=429)

                if response.status_code != 200:
                    raise ProviderError(
                        f"SerpAPI {response.status_code}: {response.text}",
                        status_code=response.status_code,
                    )

                data = response.json()
                if "error" in data:
                    raise ProviderError(data["error"], status_code=response.status_code)

                return data, latency_ms

        except httpx.RequestError as exc:
            raise ProviderError(f"Network error: {exc}") from exc

    # ── Direct Google Trends ──────────────────────────────────────────────────

    async def _try_direct_google(
        self, params: dict[str, Any], proxy_url: str | None
    ) -> tuple[dict[str, Any], int]:
        start = time.monotonic()
        data_type = params.get("data_type", "TIMESERIES")
        date = params.get("date", "today 12-m")
        geo = params.get("geo", "")

        if data_type == "TIMESERIES":
            keywords = [k.strip() for k in params.get("q", "").split(",")]
            raw = await google_trends_direct.fetch_interest_over_time(
                keywords=keywords,
                date=date,
                geo=geo,
                cat=int(params.get("cat", 0)),
                gprop=params.get("gprop", ""),
                proxy_url=proxy_url,
            )
        else:  # RELATED_QUERIES
            keyword = params.get("q", "")
            raw = await google_trends_direct.fetch_related_queries(
                keyword=keyword,
                date=date,
                geo=geo,
                proxy_url=proxy_url,
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        return raw, latency_ms

    async def _try_direct_with_proxies(
        self, params: dict[str, Any], db: AsyncSession
    ) -> tuple[dict[str, Any], int]:
        tried_proxies: set[int] = set()

        for _ in range(MAX_PROXY_RETRIES):
            proxy_entry = await proxy_pool.get_available(db)

            if proxy_entry is None:
                raise ProviderError(
                    "All SerpAPI keys exhausted and Google blocked the IP. "
                    "Add proxies via /api/v1/providers/proxies to continue."
                )

            if proxy_entry.id in tried_proxies:
                break
            tried_proxies.add(proxy_entry.id)

            try:
                data, latency_ms = await self._try_direct_google(params, proxy_url=proxy_entry.url)
                await proxy_pool.mark_used(db, proxy_entry.id)
                return data, latency_ms
            except GoogleBlockedError:
                await proxy_pool.mark_failed(db, proxy_entry.id)
                continue

        raise ProviderError(
            "All SerpAPI keys exhausted and all proxies are blocked by Google."
        )


provider_manager = ProviderManager()
