from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.manager import ProviderManager
from app.schemas.trends import (
    InterestPoint,
    RelatedQueriesRequest,
    RelatedQueriesResponse,
    RelatedQuery,
    TrendsRequest,
    TrendsResponse,
)


class TrendsService:
    def __init__(self, manager: ProviderManager):
        self._manager = manager

    async def interest_over_time(
        self, req: TrendsRequest, request_id: str, db: AsyncSession
    ) -> tuple[TrendsResponse, dict[str, Any], dict[str, Any], int]:
        date_str = req.build_serpapi_date()
        params = {
            "engine": "google_trends",
            "q": ",".join(req.q),
            "date": date_str,
            "geo": req.geo,
            "cat": req.cat,
            "gprop": req.gprop,
            "data_type": "TIMESERIES",
        }

        raw, latency_ms = await self._manager.search(params, db)

        timeline = raw.get("interest_over_time", {}).get("timeline_data", [])
        points = [
            InterestPoint(
                date=item.get("date", ""),
                values={v["query"]: v.get("value", 0) for v in item.get("values", [])},
            )
            for item in timeline
        ]

        response = TrendsResponse(
            request_id=request_id,
            keywords=req.q,
            geo=req.geo or "worldwide",
            date_range_used=date_str,
            interest_over_time=points,
            latency_ms=latency_ms,
        )
        return response, params, raw, latency_ms

    async def related_queries(
        self, req: RelatedQueriesRequest, request_id: str, db: AsyncSession
    ) -> tuple[RelatedQueriesResponse, dict[str, Any], dict[str, Any], int]:
        date_str = req.build_serpapi_date()
        params = {
            "engine": "google_trends",
            "q": req.q,
            "date": date_str,
            "geo": req.geo,
            "data_type": "RELATED_QUERIES",
        }

        raw, latency_ms = await self._manager.search(params, db)

        keyword_data = raw.get("related_queries", {}).get(req.q, {})

        def parse_queries(items: list[dict]) -> list[RelatedQuery]:
            return [RelatedQuery(query=i.get("query", ""), value=i.get("value", 0)) for i in (items or [])]

        response = RelatedQueriesResponse(
            request_id=request_id,
            keyword=req.q,
            geo=req.geo or "worldwide",
            date_range_used=date_str,
            top=parse_queries(keyword_data.get("top", [])),
            rising=parse_queries(keyword_data.get("rising", [])),
            latency_ms=latency_ms,
        )
        return response, params, raw, latency_ms
