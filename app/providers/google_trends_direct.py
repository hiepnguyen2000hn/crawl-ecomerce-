import asyncio
from typing import Any


class GoogleBlockedError(Exception):
    """Raised when Google blocks the request (IP ban / captcha)."""


class GoogleTrendsDirectClient:
    """
    Direct Google Trends client via pytrends (no SerpAPI).
    Runs in a thread pool because pytrends is synchronous.
    Used as fallback when all SerpAPI keys are exhausted.
    Proxy is only applied here — not to SerpAPI.
    """

    async def fetch_interest_over_time(
        self,
        keywords: list[str],
        date: str,
        geo: str,
        cat: int = 0,
        gprop: str = "",
        proxy_url: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._sync_interest_over_time,
            keywords, date, geo, cat, gprop, proxy_url,
        )

    def _sync_interest_over_time(
        self,
        keywords: list[str],
        date: str,
        geo: str,
        cat: int,
        gprop: str,
        proxy_url: str | None,
    ) -> dict[str, Any]:
        from pytrends.exceptions import ResponseError
        from pytrends.request import TrendReq

        proxies = [proxy_url] if proxy_url else []
        try:
            pytrends = TrendReq(hl="en-US", tz=0, proxies=proxies, timeout=(10, 25))
            pytrends.build_payload(
                kw_list=keywords,
                timeframe=date,
                geo=geo,
                cat=cat,
                gprop=gprop,
            )
            df = pytrends.interest_over_time()
        except ResponseError as exc:
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                raise GoogleBlockedError("Google blocked the request (429)") from exc
            raise GoogleBlockedError(f"Google Trends error: {exc}") from exc
        except Exception as exc:
            msg = str(exc).lower()
            if "429" in msg or "captcha" in msg or "blocked" in msg:
                raise GoogleBlockedError(f"Google blocked: {exc}") from exc
            raise

        # Convert DataFrame to SerpAPI-compatible structure
        if df.empty:
            return {"interest_over_time": {"timeline_data": []}}

        timeline_data = []
        for ts, row in df.iterrows():
            values = [
                {"query": kw, "value": int(row.get(kw, 0))}
                for kw in keywords
            ]
            timeline_data.append({
                "date": ts.strftime("%b %-d, %Y"),
                "values": values,
            })

        return {"interest_over_time": {"timeline_data": timeline_data}, "_source": "direct"}

    async def fetch_related_queries(
        self,
        keyword: str,
        date: str,
        geo: str,
        proxy_url: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._sync_related_queries,
            keyword, date, geo, proxy_url,
        )

    def _sync_related_queries(
        self,
        keyword: str,
        date: str,
        geo: str,
        proxy_url: str | None,
    ) -> dict[str, Any]:
        from pytrends.exceptions import ResponseError
        from pytrends.request import TrendReq

        proxies = [proxy_url] if proxy_url else []
        try:
            pytrends = TrendReq(hl="en-US", tz=0, proxies=proxies, timeout=(10, 25))
            pytrends.build_payload(kw_list=[keyword], timeframe=date, geo=geo)
            related = pytrends.related_queries()
        except ResponseError as exc:
            if "429" in str(exc):
                raise GoogleBlockedError("Google blocked (429)") from exc
            raise GoogleBlockedError(f"Google Trends error: {exc}") from exc
        except Exception as exc:
            if "429" in str(exc).lower():
                raise GoogleBlockedError(f"Google blocked: {exc}") from exc
            raise

        def df_to_list(df) -> list[dict]:
            if df is None or df.empty:
                return []
            return [
                {"query": row["query"], "value": str(row["value"])}
                for _, row in df.iterrows()
            ]

        kw_data = related.get(keyword, {})
        return {
            "related_queries": {
                keyword: {
                    "top": df_to_list(kw_data.get("top")),
                    "rising": df_to_list(kw_data.get("rising")),
                }
            },
            "_source": "direct",
        }


google_trends_direct = GoogleTrendsDirectClient()
