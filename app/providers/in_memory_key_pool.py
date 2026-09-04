import threading
from datetime import datetime, timedelta, timezone

COOLDOWN_MINUTES = 10


class InMemoryKeyPool:
    """
    Thread-safe in-memory key pool loaded from env vars.
    Rotates round-robin, marks keys on cooldown when rate-limited.
    State resets on container restart — acceptable for env-based keys.
    """

    def __init__(self, keys: list[str], cooldown_minutes: int = COOLDOWN_MINUTES):
        self._keys = list(keys)
        self._cooldown_minutes = cooldown_minutes
        self._cooldowns: dict[str, datetime] = {}   # key → cooldown_until
        self._usage: dict[str, int] = {}             # key → usage count
        self._index = 0
        self._lock = threading.Lock()

    def reload(self, keys: list[str]) -> None:
        """Reload keys at runtime (e.g. from remote sheet later)."""
        with self._lock:
            self._keys = list(keys)
            self._index = 0

    def get_available(self) -> str | None:
        with self._lock:
            if not self._keys:
                return None
            now = datetime.now(timezone.utc)
            # Try each key starting from current index
            for i in range(len(self._keys)):
                key = self._keys[(self._index + i) % len(self._keys)]
                cooldown_until = self._cooldowns.get(key)
                if cooldown_until is None or cooldown_until <= now:
                    self._index = (self._index + i + 1) % len(self._keys)
                    return key
            return None  # all keys on cooldown

    def mark_rate_limited(self, key: str) -> None:
        with self._lock:
            self._cooldowns[key] = datetime.now(timezone.utc) + timedelta(
                minutes=self._cooldown_minutes
            )

    def mark_used(self, key: str) -> None:
        with self._lock:
            self._usage[key] = self._usage.get(key, 0) + 1
            self._cooldowns.pop(key, None)

    def status(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        with self._lock:
            return [
                {
                    "key_preview": k[:8] + "****" + k[-4:] if len(k) > 12 else "****",
                    "usage_count": self._usage.get(k, 0),
                    "on_cooldown": (
                        self._cooldowns.get(k) is not None
                        and self._cooldowns[k] > now
                    ),
                    "cooldown_until": (
                        self._cooldowns[k].isoformat()
                        if self._cooldowns.get(k) and self._cooldowns[k] > now
                        else None
                    ),
                }
                for k in self._keys
            ]
