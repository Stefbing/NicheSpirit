"""
Session二级缓存工具
架构：
  内存缓存（最快）→ DB持久化（防丢失）→ 第三方登录（兜底）
  
每个第三方服务持有自己的 SessionCache 实例，
通过 cache_key 区分存储到 systemconfig 表的哪个 key。
"""
import time
import json
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


class SessionCache:
    """
    通用 session 二级缓存
    
    内存层：Python dict，请求级和进程级复用
    DB层：systemconfig 表，key={platform}_{suffix}，value=JSON字符串
    
    用法：
        cache = SessionCache("petkit", "session_data")
        data = await cache.get(user_id)
        if not data:
            data = await do_login()
            await cache.set(user_id, data, expires_in=1800)
    """

    def __init__(self, platform: str, key_suffix: str, ttl: int = 1800):
        """
        :param platform: 平台名（petkit/cloudpets/xiaomi）
        :param key_suffix: DB key 后缀（session_data/token）
        :param ttl: 内存缓存过期时间（秒），默认30分钟
        """
        self._platform = platform
        self._db_key = f"{platform}_{key_suffix}"
        self._memory_ttl = ttl * 1000  # 转为毫秒
        self._memory_cache: dict[int, dict] = {}  # {user_id: {data, cached_at, db_value}}

    # ── 公开接口 ──

    async def get(self, user_id: int) -> Optional[Any]:
        """获取session数据：内存 → DB"""
        data = self._from_memory(user_id)
        if data is not None:
            return data

        data = await self._from_db(user_id)
        if data is not None:
            self._to_memory(user_id, data)
            return data

        return None

    async def set(self, user_id: int, data: Any):
        """保存session数据：内存 + DB"""
        self._to_memory(user_id, data)
        await self._save_to_db(user_id, data)

    def invalidate(self, user_id: int):
        """失效指定用户的内存缓存"""
        self._memory_cache.pop(user_id, None)
        logger.debug("[SessionCache] %s 用户 %s 内存缓存已清除", self._db_key, user_id)

    def invalidate_all(self):
        """失效所有用户的内存缓存（用于服务初始化）"""
        self._memory_cache.clear()
        logger.debug("[SessionCache] %s 所有用户内存缓存已清除", self._db_key)

    # ── 内存层 ──

    def _from_memory(self, user_id: int) -> Optional[Any]:
        entry = self._memory_cache.get(user_id)
        if not entry:
            return None
        age = int(time.time() * 1000) - entry["cached_at"]
        if age > self._memory_ttl:
            self._memory_cache.pop(user_id, None)
            return None
        return entry["data"]

    def _to_memory(self, user_id: int, data: Any):
        self._memory_cache[user_id] = {
            "data": data,
            "cached_at": int(time.time() * 1000),
        }

    # ── DB层 ──

    async def _from_db(self, user_id: int) -> Optional[Any]:
        """从 systemconfig 表读取"""
        from ..utils.config_manager import get_config_from_db
        raw = await get_config_from_db(self._db_key, user_id=user_id)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw  # 纯字符串（如cloudpets token）

    async def _save_to_db(self, user_id: int, data: Any):
        """写入 systemconfig 表"""
        from ..utils.config_manager import set_config_to_db
        value = json.dumps(data) if not isinstance(data, str) else data
        await set_config_to_db(
            key=self._db_key,
            user_id=user_id,
            value=value,
            is_encrypted=False,
            platform=None,
            device_name=None,
        )
