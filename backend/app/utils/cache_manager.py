"""
内存缓存管理器 - 高性能热点数据缓存
- 惰性过期 + 后台定期清理
- 最大容量限制 + LRU 淘汰
- 异步锁竞争优化（批量操作单次加锁）
- 线程安全统计计数器
"""
import time
import asyncio
from typing import Any, Optional, Dict, List
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)

# 默认最大缓存条目数（防止内存无限增长）
DEFAULT_MAX_SIZE = 1000
# 后台清理间隔（秒）
CLEANUP_INTERVAL = 60


class CacheManager:
    """纯内存缓存管理器（TTL 自动/定期过期，LRU 淘汰，线程安全）"""
    
    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        # OrderedDict 同时维护插入顺序，用于 LRU 淘汰
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._expiry: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._max_size = max_size
        # 统计计数器（独立保护，避免与缓存操作竞争同一把锁）
        self._hit_count = 0
        self._miss_count = 0
        self._evict_count = 0
        # 后台清理任务句柄
        self._cleanup_task: Optional[asyncio.Task] = None
    
    def start_background_cleanup(self):
        """启动后台定期清理任务（应在 app lifespan 中调用）"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info(f"Cache background cleanup started (interval={CLEANUP_INTERVAL}s, max_size={self._max_size})")
    
    async def stop_background_cleanup(self):
        """停止后台清理任务"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
    
    async def _cleanup_loop(self):
        """后台定期清理过期键"""
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL)
                await self.cleanup_expired()
        except asyncio.CancelledError:
            pass
    
    def _is_expired(self, key: str) -> bool:
        """检查键是否过期（内部方法，需在锁内调用）"""
        return key in self._expiry and time.time() >= self._expiry[key]
    
    def _evict_if_needed(self):
        """LRU 淘汰：当超出容量上限时淘汰最久未访问的键（内部方法，需在锁内调用）"""
        while len(self._cache) > self._max_size:
            # OrderedDict.popitem(last=False) 移除最早插入/访问的项
            evicted_key, _ = self._cache.popitem(last=False)
            self._expiry.pop(evicted_key, None)
            self._evict_count += 1
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存（惰性过期检查 + LRU 访问提升）"""
        async with self._lock:
            if key in self._cache:
                if not self._is_expired(key):
                    # 命中：将键移到末尾（最近访问）
                    self._cache.move_to_end(key)
                    self._hit_count += 1
                    return self._cache[key]
                else:
                    # 过期，清理
                    del self._cache[key]
                    self._expiry.pop(key, None)
            self._miss_count += 1
            return None
    
    async def mget(self, keys: List[str]) -> Dict[str, Any]:
        """批量获取缓存（单次加锁，减少锁竞争）"""
        result = {}
        async with self._lock:
            current_time = time.time()
            for key in keys:
                if key in self._cache:
                    if current_time < self._expiry.get(key, 0):
                        self._cache.move_to_end(key)
                        result[key] = self._cache[key]
                        self._hit_count += 1
                    else:
                        del self._cache[key]
                        self._expiry.pop(key, None)
                        self._miss_count += 1
                else:
                    self._miss_count += 1
        return result
    
    async def set(self, key: str, value: Any, ttl: int = 300):
        """设置缓存（秒），超出容量时 LRU 淘汰"""
        if ttl <= 0:
            raise ValueError("TTL must be positive")
        
        async with self._lock:
            # 如果键已存在，先删除再重新插入（更新 OrderedDict 顺序）
            if key in self._cache:
                del self._cache[key]
            self._cache[key] = value
            self._expiry[key] = time.time() + ttl
            # 检查容量并淘汰
            self._evict_if_needed()
    
    async def mset(self, items: Dict[str, tuple], default_ttl: int = 300):
        """批量设置缓存 {key: (value, ttl)}，单次加锁"""
        async with self._lock:
            current_time = time.time()
            for key, (value, ttl) in items.items():
                if ttl <= 0:
                    ttl = default_ttl
                if key in self._cache:
                    del self._cache[key]
                self._cache[key] = value
                self._expiry[key] = current_time + ttl
            self._evict_if_needed()
    
    async def delete(self, key: str):
        """删除缓存"""
        async with self._lock:
            self._cache.pop(key, None)
            self._expiry.pop(key, None)
    
    async def mdelete(self, keys: List[str]):
        """批量删除缓存（单次加锁）"""
        async with self._lock:
            for key in keys:
                self._cache.pop(key, None)
                self._expiry.pop(key, None)
    
    async def clear(self):
        """清空所有缓存"""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._expiry.clear()
            logger.info(f"Cache cleared: {count} items removed")
    
    async def exists(self, key: str) -> bool:
        """检查键是否存在且未过期"""
        async with self._lock:
            if key in self._cache and not self._is_expired(key):
                return True
            if key in self._cache:
                del self._cache[key]
                self._expiry.pop(key, None)
            return False
    
    async def size(self) -> int:
        """返回当前有效缓存大小（顺便清理过期键）"""
        async with self._lock:
            current_time = time.time()
            expired_keys = [
                k for k, exp in self._expiry.items() 
                if current_time >= exp
            ]
            for k in expired_keys:
                self._cache.pop(k, None)
                self._expiry.pop(k, None)
            return len(self._cache)
    
    async def cleanup_expired(self):
        """清理所有过期缓存项（可由后台任务定期调用）"""
        async with self._lock:
            current_time = time.time()
            expired_keys = [
                k for k, exp in self._expiry.items() 
                if current_time >= exp
            ]
            for k in expired_keys:
                self._cache.pop(k, None)
                self._expiry.pop(k, None)
            if expired_keys:
                logger.debug(f"Cache cleanup: removed {len(expired_keys)} expired items")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息（非精确，但无锁开销）"""
        total = self._hit_count + self._miss_count
        hit_rate = (self._hit_count / total * 100) if total > 0 else 0
        return {
            'hits': self._hit_count,
            'misses': self._miss_count,
            'evictions': self._evict_count,
            'hit_rate': round(hit_rate, 2),
            'size': len(self._cache),
            'max_size': self._max_size
        }
    
    def reset_stats(self):
        """重置统计计数器"""
        self._hit_count = 0
        self._miss_count = 0
        self._evict_count = 0


# 全局缓存实例
cache_manager = CacheManager()
