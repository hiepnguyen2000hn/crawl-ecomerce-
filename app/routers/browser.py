"""
Browser Profile API — quản lý multi-account CloakBrowser sessions.

POST /api/v1/browser/profiles          — tạo profile mới (account)
GET  /api/v1/browser/profiles          — list profiles
GET  /api/v1/browser/profiles/{id}     — chi tiết
PATCH /api/v1/browser/profiles/{id}    — update proxy/label/notes
DELETE /api/v1/browser/profiles/{id}   — xoá
POST /api/v1/browser/profiles/{id}/run — chạy task trên profile (crawl URL)
GET  /api/v1/browser/pool/status       — xem pool concurrent limit
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import browser_profile as crud
from app.database import get_db
from app.services.cloak_browser import CloakBrowserError, browser_pool

router = APIRouter(prefix="/api/v1/browser", tags=["Browser Profiles"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ProfileIn(BaseModel):
    label: str
    proxy_url: str | None = None
    profile_dir: str | None = None
    notes: str | None = None
    fingerprint_seed: int | None = None  # None = auto-generate


class ProfileUpdateIn(BaseModel):
    label: str | None = None
    proxy_url: str | None = None
    profile_dir: str | None = None
    is_active: bool | None = None
    notes: str | None = None


class ProfileOut(BaseModel):
    id: int
    label: str
    fingerprint_seed: int
    proxy_url: str | None
    profile_dir: str | None
    is_active: bool
    error_count: int
    notes: str | None
    last_used_at: str | None
    created_at: str

    class Config:
        from_attributes = True


class RunTaskIn(BaseModel):
    url: str
    headless: bool = True
    wait_for: str | None = None       # CSS selector to wait for
    extract_text: bool = True         # trả về page text
    screenshot: bool = False          # chụp screenshot (base64)


class RunTaskOut(BaseModel):
    profile_id: int
    url: str
    fingerprint_seed: int
    status: str
    page_text: str | None = None
    screenshot_b64: str | None = None
    error: str | None = None


# ── CRUD Endpoints ────────────────────────────────────────────────────────────

@router.post("/profiles", response_model=ProfileOut, status_code=201)
async def create_profile(body: ProfileIn, db: Annotated[AsyncSession, Depends(get_db)]):
    return await crud.create_profile(
        db,
        label=body.label,
        proxy_url=body.proxy_url,
        profile_dir=body.profile_dir,
        notes=body.notes,
        fingerprint_seed=body.fingerprint_seed,
    )


@router.get("/profiles", response_model=list[ProfileOut])
async def list_profiles(
    db: Annotated[AsyncSession, Depends(get_db)],
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    profiles = await crud.list_profiles(db, active_only=active_only, limit=limit, offset=offset)
    return [
        ProfileOut(
            id=p.id, label=p.label, fingerprint_seed=p.fingerprint_seed,
            proxy_url=p.proxy_url, profile_dir=p.profile_dir,
            is_active=p.is_active, error_count=p.error_count, notes=p.notes,
            last_used_at=p.last_used_at.isoformat() if p.last_used_at else None,
            created_at=p.created_at.isoformat(),
        )
        for p in profiles
    ]


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
async def get_profile(profile_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    p = await crud.get_profile(db, profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfileOut(
        id=p.id, label=p.label, fingerprint_seed=p.fingerprint_seed,
        proxy_url=p.proxy_url, profile_dir=p.profile_dir,
        is_active=p.is_active, error_count=p.error_count, notes=p.notes,
        last_used_at=p.last_used_at.isoformat() if p.last_used_at else None,
        created_at=p.created_at.isoformat(),
    )


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: int, body: ProfileUpdateIn, db: Annotated[AsyncSession, Depends(get_db)]
):
    p = await crud.update_profile(
        db, profile_id,
        label=body.label, proxy_url=body.proxy_url,
        profile_dir=body.profile_dir, is_active=body.is_active, notes=body.notes,
    )
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfileOut(
        id=p.id, label=p.label, fingerprint_seed=p.fingerprint_seed,
        proxy_url=p.proxy_url, profile_dir=p.profile_dir,
        is_active=p.is_active, error_count=p.error_count, notes=p.notes,
        last_used_at=p.last_used_at.isoformat() if p.last_used_at else None,
        created_at=p.created_at.isoformat(),
    )


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    deleted = await crud.delete_profile(db, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profile not found")


# ── Run Task ──────────────────────────────────────────────────────────────────

@router.post("/profiles/{profile_id}/run", response_model=RunTaskOut)
async def run_task(
    profile_id: int,
    body: RunTaskIn,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Mở browser với fingerprint của profile, navigate đến URL, trả về nội dung.
    Dùng browser_pool để giới hạn concurrent sessions theo license.
    """
    profile = await crud.get_profile(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not profile.is_active:
        raise HTTPException(status_code=400, detail="Profile is inactive")

    try:
        async with browser_pool.acquire(
            fingerprint_seed=profile.fingerprint_seed,
            proxy_url=profile.proxy_url,
            headless=body.headless,
            profile_dir=profile.profile_dir,
        ) as session:
            await session.page.goto(body.url, wait_until="domcontentloaded", timeout=30_000)

            if body.wait_for:
                await session.page.wait_for_selector(body.wait_for, timeout=10_000)

            page_text: str | None = None
            if body.extract_text:
                # Strip tags, lấy text thuần
                page_text = await session.page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
                page_text = page_text[:15_000]

            screenshot_b64: str | None = None
            if body.screenshot:
                import base64
                png = await session.page.screenshot(type="png")
                screenshot_b64 = base64.b64encode(png).decode()

        await crud.mark_used(db, profile_id)
        return RunTaskOut(
            profile_id=profile_id,
            url=body.url,
            fingerprint_seed=profile.fingerprint_seed,
            status="success",
            page_text=page_text,
            screenshot_b64=screenshot_b64,
        )

    except Exception as exc:
        await crud.mark_error(db, profile_id)
        return RunTaskOut(
            profile_id=profile_id,
            url=body.url,
            fingerprint_seed=profile.fingerprint_seed,
            status="error",
            error=str(exc),
        )


# ── Pool Status ───────────────────────────────────────────────────────────────

@router.get("/pool/status")
async def pool_status():
    """Xem giới hạn concurrent sessions và cloakserve endpoint."""
    from app.config import settings
    return {
        "cloak_browser_url": settings.cloak_browser_url,
        "max_concurrent": browser_pool._sem._value,
        "note": "Tăng max_concurrent trong cloak_browser.py sau khi upgrade license Pro",
        "ram_estimate": {
            "per_session_idle_mb": 190,
            "per_session_3tabs_mb": 280,
            "per_extra_tab_mb": 30,
        },
    }
