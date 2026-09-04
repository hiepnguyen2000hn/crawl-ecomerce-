from app.config import settings
from app.providers.in_memory_key_pool import InMemoryKeyPool

# Initialized from env — no DB dependency
serpapi_key_pool = InMemoryKeyPool(settings.serpapi_keys, cooldown_minutes=10)
