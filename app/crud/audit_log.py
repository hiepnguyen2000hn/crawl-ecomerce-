from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import ApiAuditLog


async def create_log(
    db: AsyncSession,
    *,
    request_id: str,
    endpoint: str,
    request_params: dict[str, Any] | None,
    response_data: dict[str, Any] | None,
    status: str,
    http_status_code: int | None = None,
    latency_ms: int | None = None,
    error_message: str | None = None,
) -> ApiAuditLog:
    log = ApiAuditLog(
        request_id=request_id,
        endpoint=endpoint,
        request_params=request_params,
        response_data=response_data,
        status=status,
        http_status_code=http_status_code,
        latency_ms=latency_ms,
        error_message=error_message,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def list_logs(
    db: AsyncSession,
    *,
    endpoint: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ApiAuditLog]:
    query = select(ApiAuditLog).order_by(ApiAuditLog.created_at.desc())
    if endpoint:
        query = query.where(ApiAuditLog.endpoint == endpoint)
    if status:
        query = query.where(ApiAuditLog.status == status)
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_log_by_request_id(db: AsyncSession, request_id: str) -> ApiAuditLog | None:
    result = await db.execute(
        select(ApiAuditLog).where(ApiAuditLog.request_id == request_id)
    )
    return result.scalar_one_or_none()
