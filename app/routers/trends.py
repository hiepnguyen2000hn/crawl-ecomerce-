import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import audit_log as crud
from app.database import get_db
from app.providers.manager import ProviderError, provider_manager
from app.schemas.trends import (
    AuditLogEntry,
    RelatedQueriesRequest,
    RelatedQueriesResponse,
    TimePreset,
    TrendsRequest,
    TrendsResponse,
)
from app.services.trends_service import TrendsService

router = APIRouter(prefix="/api/v1/trends", tags=["Google Trends"])
audit_router = APIRouter(prefix="/api/v1/audit-logs", tags=["Audit Logs"])


def get_trends_service() -> TrendsService:
    return TrendsService(provider_manager)


@router.get("/presets", summary="List available time presets")
async def list_presets() -> dict:
    return {
        "presets": [
            {"key": p.name, "value": p.value, "label": p.name.replace("_", " ").title()}
            for p in TimePreset
        ]
    }


@router.post(
    "/interest-over-time",
    response_model=TrendsResponse,
    summary="Get interest over time for keywords",
)
async def interest_over_time(
    body: TrendsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[TrendsService, Depends(get_trends_service)],
) -> TrendsResponse:
    request_id = str(uuid.uuid4())
    endpoint = "/api/v1/trends/interest-over-time"

    try:
        response, req_params, raw, latency_ms = await service.interest_over_time(body, request_id, db)

        await crud.create_log(
            db,
            request_id=request_id,
            endpoint=endpoint,
            request_params=req_params,
            response_data=raw,
            status="success",
            http_status_code=200,
            latency_ms=latency_ms,
        )
        return response

    except ProviderError as exc:
        await crud.create_log(
            db,
            request_id=request_id,
            endpoint=endpoint,
            request_params=body.model_dump(mode="json"),
            response_data=None,
            status="error",
            http_status_code=exc.status_code or 502,
            latency_ms=None,
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.post(
    "/related-queries",
    response_model=RelatedQueriesResponse,
    summary="Get related queries for a keyword",
)
async def related_queries(
    body: RelatedQueriesRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[TrendsService, Depends(get_trends_service)],
) -> RelatedQueriesResponse:
    request_id = str(uuid.uuid4())
    endpoint = "/api/v1/trends/related-queries"

    try:
        response, req_params, raw, latency_ms = await service.related_queries(body, request_id, db)

        await crud.create_log(
            db,
            request_id=request_id,
            endpoint=endpoint,
            request_params=req_params,
            response_data=raw,
            status="success",
            http_status_code=200,
            latency_ms=latency_ms,
        )
        return response

    except ProviderError as exc:
        await crud.create_log(
            db,
            request_id=request_id,
            endpoint=endpoint,
            request_params=body.model_dump(mode="json"),
            response_data=None,
            status="error",
            http_status_code=exc.status_code or 502,
            latency_ms=None,
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@audit_router.get("", response_model=list[AuditLogEntry], summary="List API audit logs")
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    endpoint: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogEntry]:
    logs = await crud.list_logs(db, endpoint=endpoint, status=status, limit=limit, offset=offset)
    return [
        AuditLogEntry(
            id=log.id,
            request_id=log.request_id,
            endpoint=log.endpoint,
            request_params=log.request_params,
            status=log.status,
            http_status_code=log.http_status_code,
            latency_ms=log.latency_ms,
            error_message=log.error_message,
            created_at=log.created_at.isoformat(),
        )
        for log in logs
    ]


@audit_router.get("/{request_id}", summary="Get single audit log by request_id")
async def get_audit_log(request_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    log = await crud.get_log_by_request_id(db, request_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    return log
