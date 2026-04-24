from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheItem:
    value: Any
    expires_at: float


class CacheManager:
    def __init__(self) -> None:
        self._store: dict[str, CacheItem] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if not item:
            return None
        if item.expires_at < time.time():
            self._store.pop(key, None)
            return None
        return item.value

    def set(self, key: str, value: Any, ttl_seconds: int) -> Any:
        self._store[key] = CacheItem(value=value, expires_at=time.time() + ttl_seconds)
        return value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def status(self) -> list[dict[str, Any]]:
        now = time.time()
        return sorted(
            [
                {
                    "key": key,
                    "ttl_remaining": max(0, round(item.expires_at - now, 1)),
                    "type": type(item.value).__name__,
                }
                for key, item in self._store.items()
            ],
            key=lambda entry: entry["key"],
        )

