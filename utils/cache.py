"""Minimal in-memory TTL cache — no external deps, good enough for a
single-process bot. Keys must be hashable."""

import time


class TTLCache:
    def __init__(self, ttl: int = 300, max_size: int = 2000):
        self.ttl = ttl
        self.max_size = max_size
        self._store = {}  # key -> (value, expires_at)

    def get(self, key):
        entry = self._store.get(key)
        if not entry:
            return None
        value, expires_at = entry
        if time.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key, value):
        if len(self._store) >= self.max_size:
            # drop the oldest ~10% to keep memory bounded
            oldest = sorted(self._store.items(), key=lambda kv: kv[1][1])[: max(1, self.max_size // 10)]
            for k, _ in oldest:
                self._store.pop(k, None)
        self._store[key] = (value, time.time() + self.ttl)
