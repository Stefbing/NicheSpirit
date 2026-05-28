"""
缓存管理器（已关闭）：所有 get/set/delete 均为无操作
保留此模块仅避免大量删改 import 和调用点。上游直接查 DB 或调 API。
"""
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


class CacheManager:
    """无操作缓存 — 所有方法均为空实现"""

    async def get(self, key: str) -> Optional[Any]:
        return None

    async def set(self, key: str, value: Any, ttl: int = 300):
        pass

    async def delete(self, key: str):
        pass

    async def clear(self):
        pass

    async def size(self) -> int:
        return 0

    def stats(self) -> dict:
        return {'size': 0, 'keys': []}


cache_manager = CacheManager()
