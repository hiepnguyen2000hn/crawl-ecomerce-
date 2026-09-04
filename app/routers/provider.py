from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import provider as crud
from app.database import get_db
from app.providers.account_checker import check_account
from app.providers.key_pool import serpapi_key_pool

router = APIRouter(prefix="/api/v1/providers", tags=["Providers"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ProxyIn(BaseModel):
    label: str
    url: str  # http://user:pass@host:port


class ProxyOut(BaseModel):
    id: int
    label: str
    url: str
    is_active: bool
    failed_until: str | None
    usage_count: int
    error_count: int

    class Config:
        from_attributes = True


class ToggleIn(BaseModel):
    is_active: bool


# ── SerpAPI key status (read-only from .env) ─────────────────────────────────

@router.get("/status", summary="Check SerpAPI key pool status + remaining searches")
async def provider_status():
    pool_status = serpapi_key_pool.status()
    # Check remaining searches per key (masked)
    results = []
    from app.config import settings
    for i, key in enumerate(settings.serpapi_keys):
        account = await check_account(key)
        entry = pool_status[i] if i < len(pool_status) else {}
        results.append({
            **entry,
            "searches_left": account.searches_left,
            "plan_searches": account.plan_searches,
            "is_exhausted": account.is_exhausted,
        })
    return {
        "source": ".env → SERPAPI_KEYS",
        "total_keys": len(settings.serpapi_keys),
        "keys": results,
        "note": "To add keys, update SERPAPI_KEYS=key1,key2,key3 in .env and restart"
    }


# ── Proxies (still DB-managed — IPs rotate independently of keys) ─────────────

@router.post("/proxies", response_model=ProxyOut, status_code=201, summary="Add proxy")
async def add_proxy(body: ProxyIn, db: Annotated[AsyncSession, Depends(get_db)]):
    return await crud.add_proxy(db, label=body.label, url=body.url)


@router.get("/proxies", response_model=list[ProxyOut], summary="List all proxies")
async def list_proxies(db: Annotated[AsyncSession, Depends(get_db)]):
    return await crud.list_proxies(db)


@router.patch("/proxies/{proxy_id}/toggle", response_model=ProxyOut)
async def toggle_proxy(proxy_id: int, body: ToggleIn, db: Annotated[AsyncSession, Depends(get_db)]):
    entry = await crud.toggle_proxy(db, proxy_id, body.is_active)
    if not entry:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return entry


@router.delete("/proxies/{proxy_id}", status_code=204)
async def delete_proxy(proxy_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    deleted = await crud.delete_proxy(db, proxy_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Proxy not found")
