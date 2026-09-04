from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import provider as crud
from app.database import get_db

router = APIRouter(prefix="/api/v1/providers", tags=["Providers"])


# ---------- Schemas ----------

class KeyIn(BaseModel):
    label: str
    api_key: str


class KeyOut(BaseModel):
    id: int
    label: str
    api_key: str  # masked on output
    is_active: bool
    cooldown_until: str | None
    usage_count: int
    error_count: int

    class Config:
        from_attributes = True


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


# ---------- Keys ----------

@router.post("/keys", response_model=KeyOut, status_code=201, summary="Add SerpAPI key")
async def add_key(body: KeyIn, db: Annotated[AsyncSession, Depends(get_db)]):
    entry = await crud.add_key(db, label=body.label, api_key=body.api_key)
    return _mask_key(entry)


@router.get("/keys", response_model=list[KeyOut], summary="List all SerpAPI keys")
async def list_keys(db: Annotated[AsyncSession, Depends(get_db)]):
    keys = await crud.list_keys(db)
    return [_mask_key(k) for k in keys]


@router.patch("/keys/{key_id}/toggle", response_model=KeyOut, summary="Enable/disable a key")
async def toggle_key(key_id: int, body: ToggleIn, db: Annotated[AsyncSession, Depends(get_db)]):
    entry = await crud.toggle_key(db, key_id, body.is_active)
    if not entry:
        raise HTTPException(status_code=404, detail="Key not found")
    return _mask_key(entry)


@router.delete("/keys/{key_id}", status_code=204, summary="Delete a key")
async def delete_key(key_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    deleted = await crud.delete_key(db, key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Key not found")


# ---------- Proxies ----------

@router.post("/proxies", response_model=ProxyOut, status_code=201, summary="Add proxy")
async def add_proxy(body: ProxyIn, db: Annotated[AsyncSession, Depends(get_db)]):
    return await crud.add_proxy(db, label=body.label, url=body.url)


@router.get("/proxies", response_model=list[ProxyOut], summary="List all proxies")
async def list_proxies(db: Annotated[AsyncSession, Depends(get_db)]):
    return await crud.list_proxies(db)


@router.patch("/proxies/{proxy_id}/toggle", response_model=ProxyOut, summary="Enable/disable a proxy")
async def toggle_proxy(proxy_id: int, body: ToggleIn, db: Annotated[AsyncSession, Depends(get_db)]):
    entry = await crud.toggle_proxy(db, proxy_id, body.is_active)
    if not entry:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return entry


@router.delete("/proxies/{proxy_id}", status_code=204, summary="Delete a proxy")
async def delete_proxy(proxy_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    deleted = await crud.delete_proxy(db, proxy_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Proxy not found")


# ---------- Helpers ----------

def _mask_key(entry) -> dict:
    key = entry.api_key
    masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
    return KeyOut(
        id=entry.id,
        label=entry.label,
        api_key=masked,
        is_active=entry.is_active,
        cooldown_until=entry.cooldown_until.isoformat() if entry.cooldown_until else None,
        usage_count=entry.usage_count,
        error_count=entry.error_count,
    )
