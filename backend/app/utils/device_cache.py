"""
设备缓存管理器 - 启动时加载所有设备配置，按 user_id+platform 分组缓存
生命周期：系统启动 -> load_all()，设备增删 -> invalidate_user()
所有缓存数据存储在 Redis 中，无内存缓存
"""
import logging
import asyncio
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from sqlmodel import Session, select
from backend.app.models.db import engine
from backend.app.models.models import SystemConfig
from backend.app.utils.config_encryptor import ConfigEncryptor

logger = logging.getLogger(__name__)

# 云设备必须包含的 key
CLOUD_REQUIRED_KEYS = {'account', 'password', 'token'}
# BLE 设备必须包含的 key
BLE_REQUIRED_KEYS = {'ble_address'}


@dataclass
class DeviceRecord:
    """单个设备平台的完整记录"""
    platform: str
    device_name: str
    is_ble: bool = False
    is_shared: bool = False  # True=通过分享获取的设备
    account: str = ''
    password: str = ''
    token: str = ''
    ble_address: str = ''
    is_complete: bool = False

    @property
    def device_key(self) -> str:
        return f"{self.platform}_{self.device_name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'platform': self.platform,
            'device_name': self.device_name,
            'device_key': self.device_key,
            'is_ble': self.is_ble,
            'is_complete': self.is_complete,
            'has_account': bool(self.account),
            'has_password': bool(self.password),
            'has_token': bool(self.token),
            'has_ble_address': bool(self.ble_address),
        }


class DeviceCacheManager:
    """设备缓存管理器（Redis 持久化）"""

    DEVICE_CACHE_KEY = "device_cache:all"

    def __init__(self):
        self._loaded = False

    # ==================== 加载 & 刷新 ====================

    async def load_all(self):
        """系统启动时全量加载所有活跃设备到 Redis"""
        from backend.app.utils.redis_cache import redis_cache

        def _load():
            with Session(engine) as session:
                return session.exec(
                    select(SystemConfig).where(
                        SystemConfig.is_active == True,
                        SystemConfig.user_id > 0,
                        SystemConfig.platform.isnot(None),
                    )
                ).all()

        rows = await asyncio.get_running_loop().run_in_executor(None, _load)
        data = self._build_cache(rows)
        await redis_cache.set(self.DEVICE_CACHE_KEY, data, ttl=86400)
        self._loaded = True
        total = sum(len(plats) for plats in data.values())
        logger.info(f"[DeviceCache] 启动加载完成: {len(data)} 用户, {total} 设备平台")

    @staticmethod
    def _build_cache(rows: List[SystemConfig]) -> dict:
        """将数据库行重建为可序列化的缓存结构（纯 dict，非 DeviceRecord）"""
        temp: Dict[int, Dict[str, dict]] = {}
        for row in rows:
            uid = row.user_id
            plat = row.platform
            if uid not in temp:
                temp[uid] = {}
            if plat not in temp[uid]:
                temp[uid][plat] = {
                    'platform': plat,
                    'device_name': row.device_name or plat,
                    'keys': {},
                }
            val = row.value
            if row.is_encrypted:
                try:
                    val = ConfigEncryptor.decrypt(val)
                except Exception:
                    val = ''
            temp[uid][plat]['keys'][row.key] = val

        result: Dict[str, Dict[str, dict]] = {}
        for uid, plats in temp.items():
            uid_str = str(uid)
            result[uid_str] = {}
            for plat, data in plats.items():
                keys = data['keys']
                is_ble = 'ble_address' in keys
                required = BLE_REQUIRED_KEYS if is_ble else CLOUD_REQUIRED_KEYS
                present = set(keys.keys())
                result[uid_str][plat] = {
                    'platform': plat,
                    'device_name': data['device_name'],
                    'is_ble': is_ble,
                    'account': keys.get('account', ''),
                    'password': keys.get('password', ''),
                    'token': keys.get('token', ''),
                    'ble_address': keys.get('ble_address', ''),
                    'is_complete': required.issubset(present),
                }
        return result

    # ==================== 查询接口 ====================

    async def get_user_platforms(self, user_id: int) -> Dict[str, 'DeviceRecord']:
        """
        获取用户的设备平台映射。
        首次调用或缓存失效时从 DB 重建。
        """
        from backend.app.utils.redis_cache import redis_cache

        data = await redis_cache.get(self.DEVICE_CACHE_KEY)
        if data is None:
            await self.load_all()
            data = await redis_cache.get(self.DEVICE_CACHE_KEY)

        if data is None:
            return {}

        platforms = data.get(str(user_id))
        if platforms is None:
            await self._load_user_from_db(user_id)
            data = await redis_cache.get(self.DEVICE_CACHE_KEY)
            platforms = data.get(str(user_id), {}) if data else {}

        result: Dict[str, DeviceRecord] = {}
        for plat, info in platforms.items():
            if isinstance(info, dict):
                result[plat] = DeviceRecord(**info)
            else:
                result[plat] = info
        return result

    async def _load_user_from_db(self, user_id: int):
        """从数据库加载单个用户的设备到 Redis"""
        from backend.app.utils.redis_cache import redis_cache

        def _load():
            with Session(engine) as session:
                return session.exec(
                    select(SystemConfig).where(
                        SystemConfig.is_active == True,
                        SystemConfig.user_id == user_id,
                        SystemConfig.platform.isnot(None),
                    )
                ).all()

        rows = await asyncio.get_running_loop().run_in_executor(None, _load)
        existing = await redis_cache.get(self.DEVICE_CACHE_KEY) or {}
        if not rows:
            existing[str(user_id)] = {}
            await redis_cache.set(self.DEVICE_CACHE_KEY, existing, ttl=86400)
            return

        new_user_data = self._build_cache(rows).get(str(user_id), {})
        existing[str(user_id)] = new_user_data
        await redis_cache.set(self.DEVICE_CACHE_KEY, existing, ttl=86400)

    # ==================== 缓存失效 ====================

    async def invalidate_user(self, user_id: int):
        """用户设备变更后清除缓存，下次查询时自动重载"""
        from backend.app.utils.redis_cache import redis_cache
        data = await redis_cache.get(self.DEVICE_CACHE_KEY)
        if data:
            data.pop(str(user_id), None)
            await redis_cache.set(self.DEVICE_CACHE_KEY, data, ttl=86400)

    async def invalidate_platform(self, user_id: int, platform: str):
        """清除单个用户单个平台的缓存"""
        from backend.app.utils.redis_cache import redis_cache
        data = await redis_cache.get(self.DEVICE_CACHE_KEY)
        if data and str(user_id) in data:
            data[str(user_id)].pop(platform, None)
            await redis_cache.set(self.DEVICE_CACHE_KEY, data, ttl=86400)

    async def invalidate_all(self):
        """清空全部缓存"""
        from backend.app.utils.redis_cache import redis_cache
        await redis_cache.delete(self.DEVICE_CACHE_KEY)
        self._loaded = False

    # ==================== 统计 ====================

    async def stats(self) -> Dict[str, Any]:
        from backend.app.utils.redis_cache import redis_cache
        data = await redis_cache.get(self.DEVICE_CACHE_KEY) or {}
        total_users = len(data)
        total_platforms = sum(len(plats) for plats in data.values())
        return {
            'loaded': self._loaded,
            'users': total_users,
            'platforms': total_platforms,
        }


# 全局单例
device_cache = DeviceCacheManager()
