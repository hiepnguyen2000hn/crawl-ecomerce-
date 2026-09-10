import time

import httpx

from app.config import settings
from app.providers.in_memory_key_pool import InMemoryKeyPool

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MAX_RETRIES = 3

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


# In-memory pool — loaded from env, no DB
openrouter_key_pool = InMemoryKeyPool(settings.openrouter_api_keys, cooldown_minutes=5)


class OpenRouterClient:
    async def chat(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model: str = DEFAULT_FREE_MODEL,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> tuple[str, str, int, int, int]:
        """
        Returns (raw_text, model_used, tokens_prompt, tokens_completion, latency_ms).
        Rotates keys on 429.
        """
        tried: set[str] = set()

        for _ in range(MAX_RETRIES):
            api_key = openrouter_key_pool.get_available()
            if api_key is None or api_key in tried:
                raise AllAiKeysExhausted("No OpenRouter API key available. Add keys to OPENROUTER_API_KEYS in .env")
            tried.add(api_key)

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                # Không tắt reasoning thì model sinh chain-of-thought dài, ăn hết max_tokens
                # trước khi kịp in JSON cuối cùng → parse_error ở app/crud/ai.py.
                "reasoning": {"enabled": False},
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
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
                        openrouter_key_pool.mark_rate_limited(api_key)
                        continue

                    if resp.status_code != 200:
                        raise OpenRouterError(f"OpenRouter {resp.status_code}: {resp.text}", status_code=resp.status_code)

                    data = resp.json()
                    openrouter_key_pool.mark_used(api_key)

                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    return (
                        content,
                        data.get("model", model),
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        latency_ms,
                    )

            except httpx.RequestError as exc:
                raise OpenRouterError(f"Network error: {exc}") from exc

        raise AllAiKeysExhausted(f"All {len(tried)} OpenRouter key(s) rate-limited")

    async def list_free_models(self) -> list[dict]:
        api_key = openrouter_key_pool.get_available()
        if not api_key:
            return [{"id": m, "name": m} for m in FREE_MODELS]
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(OPENROUTER_MODELS_URL, headers=headers)
                if resp.status_code != 200:
                    return [{"id": m, "name": m} for m in FREE_MODELS]
                return [
                    {"id": m["id"], "name": m.get("name", m["id"]), "context_length": m.get("context_length", 0)}
                    for m in resp.json().get("data", [])
                    if ":free" in m.get("id", "")
                ]
        except Exception:
            return [{"id": m, "name": m} for m in FREE_MODELS]


openrouter_client = OpenRouterClient()
