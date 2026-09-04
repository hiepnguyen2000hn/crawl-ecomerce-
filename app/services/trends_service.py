from typing import Any

from app.schemas.trends import (
    InterestPoint,
    RelatedQueriesRequest,
    RelatedQueriesResponse,
    RelatedQuery,
    TrendsRequest,
    TrendsResponse,
)
from app.services.serpapi_client import SerpApiClient, SerpApiError


class TrendsService:
    def __init__(self, client: SerpApiClient):
        self._client = client

    async def interest_over_time(
        self, req: TrendsRequest, request_id: str
    ) -> tuple[TrendsResponse, dict[str, Any], dict[str, Any], int]:
        """
        Returns (response, request_params, raw_response, latency_ms).
        request_params and raw_response are used for audit logging.
        """
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

        raw, latency_ms = await self._client.search(params)

        interest_data = raw.get("interest_over_time", {})
        timeline = interest_data.get("timeline_data", [])

        points = []
        for item in timeline:
            values = {
                v["query"]: v.get("value", 0)
                for v in item.get("values", [])
            }
            points.append(InterestPoint(date=item.get("date", ""), values=values))

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
        self, req: RelatedQueriesRequest, request_id: str
    ) -> tuple[RelatedQueriesResponse, dict[str, Any], dict[str, Any], int]:
        date_str = req.build_serpapi_date()
        params = {
            "engine": "google_trends",
            "q": req.q,
            "date": date_str,
            "geo": req.geo,
            "data_type": "RELATED_QUERIES",
        }

        raw, latency_ms = await self._client.search(params)

        related = raw.get("related_queries", {})
        keyword_data = related.get(req.q, {})

        def parse_queries(items: list[dict]) -> list[RelatedQuery]:
            return [
                RelatedQuery(query=i.get("query", ""), value=i.get("value", 0))
                for i in (items or [])
            ]

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
