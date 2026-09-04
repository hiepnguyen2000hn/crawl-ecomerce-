import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_TEMPLATE,
    AiAnalysisResult,
    AiProviderKey,
    MasterPrompt,
)

ACTIVE_PROMPT_NAME = "price_extractor"


# ── Master Prompt ──────────────────────────────────────────────────────────────

async def get_active_prompt(db: AsyncSession) -> MasterPrompt:
    result = await db.execute(
        select(MasterPrompt)
        .where(MasterPrompt.name == ACTIVE_PROMPT_NAME)
        .where(MasterPrompt.is_active == True)
    )
    prompt = result.scalar_one_or_none()

    if prompt is None:
        # Seed default on first use
        prompt = MasterPrompt(
            name=ACTIVE_PROMPT_NAME,
            description="Default price extractor prompt",
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            user_template=DEFAULT_USER_TEMPLATE,
        )
        db.add(prompt)
        await db.commit()
        await db.refresh(prompt)

    return prompt


async def update_prompt(
    db: AsyncSession,
    *,
    system_prompt: str | None = None,
    user_template: str | None = None,
    description: str | None = None,
) -> MasterPrompt:
    prompt = await get_active_prompt(db)
    if system_prompt is not None:
        prompt.system_prompt = system_prompt
    if user_template is not None:
        prompt.user_template = user_template
    if description is not None:
        prompt.description = description
    await db.commit()
    await db.refresh(prompt)
    return prompt


# ── AI Provider Keys ───────────────────────────────────────────────────────────

async def add_ai_key(db: AsyncSession, label: str, api_key: str, provider: str = "openrouter") -> AiProviderKey:
    entry = AiProviderKey(label=label, api_key=api_key, provider=provider)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_ai_keys(db: AsyncSession, provider: str = "openrouter") -> list[AiProviderKey]:
    result = await db.execute(
        select(AiProviderKey).where(AiProviderKey.provider == provider).order_by(AiProviderKey.id)
    )
    return list(result.scalars().all())


async def toggle_ai_key(db: AsyncSession, key_id: int, is_active: bool) -> AiProviderKey | None:
    result = await db.execute(select(AiProviderKey).where(AiProviderKey.id == key_id))
    entry = result.scalar_one_or_none()
    if entry:
        entry.is_active = is_active
        await db.commit()
        await db.refresh(entry)
    return entry


async def delete_ai_key(db: AsyncSession, key_id: int) -> bool:
    result = await db.execute(select(AiProviderKey).where(AiProviderKey.id == key_id))
    entry = result.scalar_one_or_none()
    if entry:
        await db.delete(entry)
        await db.commit()
        return True
    return False


# ── AI Results ────────────────────────────────────────────────────────────────

async def save_analysis_result(
    db: AsyncSession,
    *,
    request_id: str,
    target_url: str,
    model_used: str,
    prompt_name: str,
    raw_response: str,
    tokens_prompt: int,
    tokens_completion: int,
    latency_ms: int,
) -> AiAnalysisResult:
    # Try to parse JSON from AI response
    try:
        extracted = json.loads(raw_response)
    except Exception:
        # AI may wrap JSON in markdown — strip code blocks
        cleaned = raw_response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            extracted = json.loads(cleaned)
        except Exception:
            extracted = {"raw": raw_response, "parse_error": True}

    row = AiAnalysisResult(
        request_id=request_id,
        target_url=target_url,
        model_used=model_used,
        prompt_name=prompt_name,
        extracted_data=extracted,
        raw_response=raw_response,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        latency_ms=latency_ms,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_analysis_results(
    db: AsyncSession,
    *,
    url_contains: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AiAnalysisResult]:
    q = select(AiAnalysisResult).order_by(AiAnalysisResult.created_at.desc())
    if url_contains:
        q = q.where(AiAnalysisResult.target_url.ilike(f"%{url_contains}%"))
    result = await db.execute(q.limit(limit).offset(offset))
    return list(result.scalars().all())
