"""
轻量内存缓存管理器 — 仅保留 get/set/delete，无 LRU/后台清理
TTL 惰性过期，单线程 asyncio 无需锁
"""
import time
import logging
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)


class CacheManager:
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存，过期返回 None"""
        if key not in self._cache:
            return None
        if time.time() >= self._expiry.get(key, 0):
            del self._cache[key]
            self._expiry.pop(key, None)
            return None
        return self._cache[key]

    async def set(self, key: str, value: Any, ttl: int = 300):
        """设置缓存（秒）"""
        self._cache[key] = value
        self._expiry[key] = time.time() + ttl

    async def delete(self, key: str):
        """删除缓存"""
        self._cache.pop(key, None)
        self._expiry.pop(key, None)

    async def clear(self):
        """清空所有缓存"""
        self._cache.clear()
        self._expiry.clear()

    async def size(self) -> int:
        """返回有效缓存数量（顺便清理过期）"""
        now = time.time()
        expired = [k for k, exp in self._expiry.items() if now >= exp]
        for k in expired:
            self._cache.pop(k, None)
            self._expiry.pop(k, None)
        return len(self._cache)

    def stats(self) -> dict:
        return {'size': len(self._cache), 'keys': list(self._cache.keys())}


cache_manager = CacheManager()
