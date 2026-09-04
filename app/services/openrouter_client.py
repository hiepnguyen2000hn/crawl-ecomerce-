import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AiProviderKey

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
COOLDOWN_MINUTES = 5
MAX_RETRIES = 3

# Curated free models — appended with :free by OpenRouter convention
FREE_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "thinkingmachines/inkling-small:free",
    "dots-studio/dots-3-note-preview:free",
    "poolside/laguna-s-2.1:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "liquid/lfm-2.5-2.6b:free",
]
DEFAULT_FREE_MODEL = FREE_MODELS[0]


class OpenRouterError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AllAiKeysExhausted(Exception):
    pass


class OpenRouterClient:
    async def _get_available_key(self, db: AsyncSession) -> AiProviderKey | None:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(AiProviderKey)
            .where(AiProviderKey.is_active == True)
            .where(AiProviderKey.provider == "openrouter")
            .where(
                (AiProviderKey.cooldown_until == None)
                | (AiProviderKey.cooldown_until <= now)
            )
            .order_by(AiProviderKey.usage_count.asc())
        )
        return result.scalars().first()

    async def _mark_cooldown(self, db: AsyncSession, key_id: int) -> None:
        cooldown = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
        await db.execute(
            update(AiProviderKey)
            .where(AiProviderKey.id == key_id)
            .values(cooldown_until=cooldown, error_count=AiProviderKey.error_count + 1)
        )
        await db.commit()

    async def _mark_used(self, db: AsyncSession, key_id: int) -> None:
        await db.execute(
            update(AiProviderKey)
            .where(AiProviderKey.id == key_id)
            .values(usage_count=AiProviderKey.usage_count + 1, cooldown_until=None)
        )
        await db.commit()

    async def chat(
        self,
        db: AsyncSession,
        *,
        system_prompt: str,
        user_message: str,
        model: str = DEFAULT_FREE_MODEL,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> tuple[str, str, int, int, int]:
        """
        Returns (raw_text, model_used, tokens_prompt, tokens_completion, latency_ms).
        Rotates keys on 429. Raises AllAiKeysExhausted if all keys fail.
        """
        tried: set[int] = set()

        for _ in range(MAX_RETRIES):
            key_entry = await self._get_available_key(db)
            if key_entry is None or key_entry.id in tried:
                raise AllAiKeysExhausted("No OpenRouter API key available")
            tried.add(key_entry.id)

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {
                "Authorization": f"Bearer {key_entry.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://crawl-ecomerce.local",
                "X-Title": "Crawl E-Commerce",
            }

            start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
                    latency_ms = int((time.monotonic() - start) * 1000)

                    if resp.status_code == 429:
                        await self._mark_cooldown(db, key_entry.id)
                        continue

                    if resp.status_code != 200:
                        raise OpenRouterError(
                            f"OpenRouter {resp.status_code}: {resp.text}",
                            status_code=resp.status_code,
                        )

                    data = resp.json()
                    await self._mark_used(db, key_entry.id)

                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    model_used = data.get("model", model)

                    return (
                        content,
                        model_used,
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        latency_ms,
                    )

            except httpx.RequestError as exc:
                raise OpenRouterError(f"Network error: {exc}") from exc

        raise AllAiKeysExhausted(f"All {len(tried)} OpenRouter key(s) rate-limited")

    async def list_free_models(self, api_key: str) -> list[dict]:
        """Fetch live free model list from OpenRouter."""
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(OPENROUTER_MODELS_URL, headers=headers)
                if resp.status_code != 200:
                    return [{"id": m, "name": m} for m in FREE_MODELS]
                models = resp.json().get("data", [])
                return [
                    {
                        "id": m["id"],
                        "name": m.get("name", m["id"]),
                        "context_length": m.get("context_length", 0),
                        "pricing": m.get("pricing", {}),
                    }
                    for m in models
                    if ":free" in m.get("id", "")
                ]
        except Exception:
            return [{"id": m, "name": m} for m in FREE_MODELS]


openrouter_client = OpenRouterClient()
