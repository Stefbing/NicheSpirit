"""
统一的 Redis 缓存层 — 全量缓存操作统一通过此模块
替代旧 cache_manager（内存缓存）、device_cache（内存缓存）、_memory_session（内存缓存）等
当 Redis 不可用时抛出异常，不降级到内存（确保数据一致性）
"""
import json
import logging
import os
import random
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 【防雪崩】最大 jitter 比例：TTL * 20% 的随机偏移，将过期时间打散
_JITTER_RATIO = 0.2


def _with_jitter(ttl: int) -> int:
    """为 TTL 添加随机偏移，防止大量 Key 同时过期"""
    offset = random.randint(0, max(1, int(ttl * _JITTER_RATIO)))
    return ttl + offset


class RedisCache:
    def __init__(self):
        self._redis = None
        self._connected = False
        # 缓存命中率统计
        self._hit_count = 0
        self._miss_count = 0

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

    async def get(self, key: str, default=None) -> Optional[Any]:
        """
        获取缓存，带结构化日志和命中率统计
        Returns:
            缓存值或default
        """
        if not self._connected or not self._redis:
            logger.warning(f"[Cache] Redis未连接, key={key}")
            return default
        
        try:
            data = await self._redis.get(key)
            if data:
                # 缓存命中
                self._hit_count += 1
                # 获取TTL
                ttl = await self._redis.ttl(key)
                logger.info(f"[Cache] HIT key={key} ttl={ttl}s")
                return json.loads(data)
            else:
                # 缓存未命中
                self._miss_count += 1
                logger.info(f"[Cache] MISS key={key}")
                return default
        except Exception as e:
            logger.error(f"[Cache] get失败 key={key} err={e}")
            return default
    
    async def set(self, key: str, value: Any, ttl: int = 300, nx=False):
        """
        设置缓存，带TTL jitter防雪崩
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 基准过期秒数
            nx: 是否使用NX模式（仅当key不存在时设置）
        """
        if not self._connected or not self._redis:
            return
        
        try:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            actual_ttl = _with_jitter(ttl)
            
            if nx:
                # 仅当key不存在时设置
                result = await self._redis.set(key, serialized, ex=actual_ttl, nx=True)
                if result:
                    logger.info(f"[Cache] SET NX key={key} ttl={actual_ttl}s")
                else:
                    logger.info(f"[Cache] SET NX 失败（key已存在） key={key}")
            else:
                await self._redis.setex(key, actual_ttl, serialized)
                logger.info(f"[Cache] SET key={key} ttl={actual_ttl}s")
        except Exception as e:
            logger.error(f"[Cache] set失败 key={key} err={e}")
    
    async def delete(self, key: str):
        """删除指定key"""
        if not self._connected or not self._redis:
            return
        try:
            result = await self._redis.delete(key)
            if result:
                logger.info(f"[Cache] DEL key={key}")
            else:
                logger.info(f"[Cache] DEL 失败（key不存在） key={key}")
        except Exception as e:
            logger.warning(f"[Cache] delete({key}) 失败: {e}")
    
    async def delete_pattern(self, pattern: str):
        """
        批量删除匹配pattern的key
        ⚠️ 生产环境慎用，可能影响性能
        """
        if not self._connected or not self._redis:
            return
        
        try:
            # 使用SCAN命令，避免阻塞
            count = 0
            async for key in self._redis.scan_iter(match=pattern, count=100):
                await self._redis.delete(key)
                count += 1
            
            if count > 0:
                logger.info(f"[Cache] DEL pattern={pattern} count={count}")
        except Exception as e:
            logger.error(f"[Cache] delete_pattern失败 pattern={pattern} err={e}")
    
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        if not self._connected or not self._redis:
            return False
        try:
            return bool(await self._redis.exists(key))
        except Exception:
            return False
    
    async def get_ttl(self, key: str) -> int:
        """获取key的剩余TTL（秒）"""
        if not self._connected or not self._redis:
            return -2
        try:
            return await self._redis.ttl(key)
        except Exception:
            return -2
    
    def get_stats(self) -> dict:
        """
        获取缓存命中率统计
        Returns:
            包含命中次数、未命中次数、命中率的字典
        """
        total = self._hit_count + self._miss_count
        hit_rate = (self._hit_count / total * 100) if total > 0 else 0
        return {
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "total": total,
            "hit_rate": round(hit_rate, 2),
        }
    
    def reset_stats(self):
        """重置缓存命中率统计"""
        self._hit_count = 0
        self._miss_count = 0

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
