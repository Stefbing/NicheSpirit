"""
已废弃 — 所有缓存操作迁移至 redis_cache
保留此文件仅为引用兼容，实际使用请导入 backend.app.utils.redis_cache.redis_cache
"""
import warnings
warnings.warn(
    "cache_manager 已废弃，请使用 backend.app.utils.redis_cache.redis_cache",
    DeprecationWarning,
    stacklevel=2,
)

from .redis_cache import redis_cache

# 兼容旧接口：cache_manager.get/set/delete → redis_cache.get/set/delete
cache_manager = redis_cache
