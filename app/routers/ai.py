import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import ai as ai_crud
from app.crud import audit_log as audit_crud
from app.database import get_db
from app.services.openrouter_client import (
    FREE_MODELS,
    AllAiKeysExhausted,
    OpenRouterError,
    openrouter_client,
    openrouter_key_pool,
)
from app.providers.key_pool import serpapi_key_pool
from app.services.web_fetcher import WebFetchError, fetch_page_text

router = APIRouter(prefix="/api/v1/ai", tags=["AI Analysis"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    url: str
    model: str = FREE_MODELS[0]
    max_tokens: int = 2048
    temperature: float = 0.1


class AnalyzeResponse(BaseModel):
    request_id: str
    url: str
    model_used: str
    prompt_name: str
    extracted_data: dict
    tokens_prompt: int
    tokens_completion: int
    latency_ms: int


class PromptOut(BaseModel):
    id: int
    name: str
    description: str
    system_prompt: str
    user_template: str
    updated_at: str


class PromptUpdateIn(BaseModel):
    system_prompt: str | None = None
    user_template: str | None = None
    description: str | None = None


# ── Analyze ───────────────────────────────────────────────────────────────────

@router.post("/analyze", response_model=AnalyzeResponse, summary="Crawl URL and extract prices via AI")
async def analyze_url(
    body: AnalyzeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnalyzeResponse:
    request_id = str(uuid.uuid4())
    endpoint = "/api/v1/ai/analyze"

    try:
        page_text = await fetch_page_text(body.url)
    except WebFetchError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot fetch URL: {exc}")

    prompt = await ai_crud.get_active_prompt(db)
    user_message = prompt.user_template.format(url=body.url, content=page_text)

    try:
        raw, model_used, tok_p, tok_c, latency_ms = await openrouter_client.chat(
            system_prompt=prompt.system_prompt,
            user_message=user_message,
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except (OpenRouterError, AllAiKeysExhausted) as exc:
        await audit_crud.create_log(
            db, request_id=request_id, endpoint=endpoint,
            request_params={"url": body.url, "model": body.model},
            response_data=None, status="error",
            http_status_code=getattr(exc, "status_code", None) or 502,
            latency_ms=None, error_message=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc))

    result = await ai_crud.save_analysis_result(
        db, request_id=request_id, target_url=body.url,
        model_used=model_used, prompt_name=prompt.name,
        raw_response=raw, tokens_prompt=tok_p,
        tokens_completion=tok_c, latency_ms=latency_ms,
    )
    await audit_crud.create_log(
        db, request_id=request_id, endpoint=endpoint,
        request_params={"url": body.url, "model": body.model},
        response_data={"model_used": model_used, "tokens": tok_p + tok_c},
        status="success", http_status_code=200, latency_ms=latency_ms,
    )

    return AnalyzeResponse(
        request_id=request_id, url=body.url, model_used=model_used,
        prompt_name=prompt.name, extracted_data=result.extracted_data,
        tokens_prompt=tok_p, tokens_completion=tok_c, latency_ms=latency_ms,
    )


# ── Master Prompt ─────────────────────────────────────────────────────────────

@router.get("/master-prompt", response_model=PromptOut, summary="Get active master prompt")
async def get_master_prompt(db: Annotated[AsyncSession, Depends(get_db)]):
    p = await ai_crud.get_active_prompt(db)
    return PromptOut(id=p.id, name=p.name, description=p.description,
                     system_prompt=p.system_prompt, user_template=p.user_template,
                     updated_at=p.updated_at.isoformat())


@router.put("/master-prompt", response_model=PromptOut, summary="Update master prompt")
async def update_master_prompt(body: PromptUpdateIn, db: Annotated[AsyncSession, Depends(get_db)]):
    if not any([body.system_prompt, body.user_template, body.description]):
        raise HTTPException(status_code=422, detail="At least one field required")
    p = await ai_crud.update_prompt(db, system_prompt=body.system_prompt,
                                     user_template=body.user_template, description=body.description)
    return PromptOut(id=p.id, name=p.name, description=p.description,
                     system_prompt=p.system_prompt, user_template=p.user_template,
                     updated_at=p.updated_at.isoformat())


# ── Models ────────────────────────────────────────────────────────────────────

@router.get("/models", summary="List available free OpenRouter models")
async def list_models():
    models = await openrouter_client.list_free_models()
    return {"models": models, "default": FREE_MODELS[0]}


# ── Key pool status (read-only, no create/delete) ─────────────────────────────

@router.get("/keys/status", summary="View OpenRouter key pool status (from .env)")
async def key_status():
    return {
        "source": ".env → OPENROUTER_API_KEYS",
        "keys": openrouter_key_pool.status(),
        "note": "To add keys, update OPENROUTER_API_KEYS in .env and restart"
    }


@router.get("/serpapi/status", summary="View SerpAPI key pool status (from .env)", tags=["AI Analysis"])
async def serpapi_status():
    return {
        "source": ".env → SERPAPI_KEYS",
        "keys": serpapi_key_pool.status(),
        "note": "To add keys, update SERPAPI_KEYS in .env and restart"
    }


# ── Results ───────────────────────────────────────────────────────────────────

@router.get("/results", summary="List AI analysis results")
async def list_results(
    db: Annotated[AsyncSession, Depends(get_db)],
    url: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    rows = await ai_crud.list_analysis_results(db, url_contains=url, limit=limit, offset=offset)
    return [
        {
            "id": r.id, "request_id": r.request_id, "target_url": r.target_url,
            "model_used": r.model_used, "prompt_name": r.prompt_name,
            "total_products": len(r.extracted_data.get("products", [])),
            "tokens_total": r.tokens_prompt + r.tokens_completion,
            "latency_ms": r.latency_ms, "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
