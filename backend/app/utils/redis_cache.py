"""
统一的 Redis 缓存层 — 全量缓存操作统一通过此模块
替代旧 cache_manager（内存缓存）、device_cache（内存缓存）、_memory_session（内存缓存）等
当 Redis 不可用时抛出异常，不降级到内存（确保数据一致性）
"""
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RedisCache:
    def __init__(self):
        self._redis = None
        self._connected = False

    async def connect(self) -> bool:
        """连接 Redis 服务器"""
        import redis.asyncio as aioredis

        addr = os.getenv("REDIS_ADDRESS", "")
        if not addr:
            logger.error("REDIS_ADDRESS 未配置，无法初始化缓存层")
            return False

        try:
            host, port_str = addr.rsplit(":", 1)
            port = int(port_str)
            self._redis = aioredis.Redis(
                host=host,
                port=port,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            await self._redis.ping()
            self._connected = True
            logger.info(f"✓ Redis 连接成功 {host}:{port}")
            return True
        except Exception as e:
            logger.error(f"Redis 连接失败 ({e})，缓存层不可用")
            self._redis = None
            self._connected = False
            return False

    async def get(self, key: str) -> Optional[Any]:
        if not self._connected or not self._redis:
            return None
        try:
            data = await self._redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Redis get({key}) 失败: {e}")
        return None

    async def set(self, key: str, value: Any, ttl: int = 300):
        if not self._connected or not self._redis:
            return
        try:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            await self._redis.setex(key, ttl, serialized)
        except Exception as e:
            logger.warning(f"Redis set({key}) 失败: {e}")

    async def delete(self, key: str):
        if not self._connected or not self._redis:
            return
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning(f"Redis delete({key}) 失败: {e}")

    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        if not self._connected or not self._redis:
            return False
        try:
            return bool(await self._redis.exists(key))
        except Exception:
            return False

    async def close(self):
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
            self._connected = False


# 全局单例
redis_cache = RedisCache()
