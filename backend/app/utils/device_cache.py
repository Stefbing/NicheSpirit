"""
设备缓存管理器 - 启动时加载所有设备配置，按 user_id+platform 分组缓存
生命周期：系统启动 → load_all()，设备增删 → invalidate_user()
"""
import logging
import time
import asyncio
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from sqlmodel import Session, select
from ..models.db import engine
from ..models.models import SystemConfig
from .config_encryptor import ConfigEncryptor

logger = logging.getLogger(__name__)

# 云设备必须包含的 key
CLOUD_REQUIRED_KEYS = {'account', 'password', 'token'}
# BLE 设备必须包含的 key
BLE_REQUIRED_KEYS = {'ble_address'}


@dataclass
class DeviceRecord:
    """单个设备平台的完整记录"""
    platform: str
    device_name: str          # 真实设备名（如"小佩智能全自动猫厕所 MAX2"）
    is_ble: bool = False       # 是否为本地蓝牙设备
    account: str = ''
    password: str = ''
    token: str = ''
    ble_address: str = ''
    is_complete: bool = False  # 是否包含该平台所需的所有 key

    @property
    def device_key(self) -> str:
        """设备标识符：platform_device_name（用于删除等操作）"""
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
    """设备缓存管理器（纯内存+惰性过期）"""

    def __init__(self):
        # { user_id: { platform: DeviceRecord } }
        self._devices: Dict[int, Dict[str, DeviceRecord]] = {}
        self._loaded = False

    # ==================== 加载 & 刷新 ====================

    async def load_all(self):
        """系统启动时全量加载所有活跃设备"""
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
        self._build_cache(rows)
        self._loaded = True
        total = sum(len(plats) for plats in self._devices.values())
        logger.info(f"[DeviceCache] 启动加载完成: {len(self._devices)} 用户, {total} 设备平台")

    def _build_cache(self, rows: List[SystemConfig]):
        """将数据库行重建为缓存结构"""
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
            # 解密凭据值后存入
            val = row.value
            if row.is_encrypted:
                try:
                    val = ConfigEncryptor.decrypt(val)
                except Exception:
                    val = ''
            temp[uid][plat]['keys'][row.key] = val

        # 构建 DeviceRecord
        result: Dict[int, Dict[str, DeviceRecord]] = {}
        for uid, plats in temp.items():
            result[uid] = {}
            for plat, data in plats.items():
                keys = data['keys']
                is_ble = 'ble_address' in keys
                required = BLE_REQUIRED_KEYS if is_ble else CLOUD_REQUIRED_KEYS
                present = set(keys.keys())
                rec = DeviceRecord(
                    platform=plat,
                    device_name=data['device_name'],
                    is_ble=is_ble,
                    account=keys.get('account', ''),
                    password=keys.get('password', ''),
                    token=keys.get('token', ''),
                    ble_address=keys.get('ble_address', ''),
                    is_complete=required.issubset(present),
                )
                result[uid][plat] = rec

        self._devices = result

    # ==================== 查询接口 ====================

    async def get_user_platforms(self, user_id: int) -> Dict[str, DeviceRecord]:
        """
        获取用户的设备平台映射。
        若缓存未加载或该用户平台不完整，回退查 DB 并更新缓存。
        """
        # 缓存未加载时尝试从 DB 加载
        if not self._loaded:
            await self.load_all()

        platforms = self._devices.get(user_id)
        if platforms is None:
            # 查 DB 确认
            await self._load_user_from_db(user_id)
            platforms = self._devices.get(user_id, {})

        return platforms

    async def _load_user_from_db(self, user_id: int):
        """从数据库加载单个用户的设备到缓存"""
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
        if not rows:
            self._devices[user_id] = {}
            return

        # 临时重建该用户的数据
        temp: Dict[int, Dict[str, dict]] = {user_id: {}}
        for row in rows:
            plat = row.platform
            if plat not in temp[user_id]:
                temp[user_id][plat] = {
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
            temp[user_id][plat]['keys'][row.key] = val

        # 合并到全局缓存（覆盖该用户）
        uid_data = temp[user_id]
        result: Dict[str, DeviceRecord] = {}
        for plat, data in uid_data.items():
            keys = data['keys']
            is_ble = 'ble_address' in keys
            required = BLE_REQUIRED_KEYS if is_ble else CLOUD_REQUIRED_KEYS
            present = set(keys.keys())
            result[plat] = DeviceRecord(
                platform=plat,
                device_name=data['device_name'],
                is_ble=is_ble,
                account=keys.get('account', ''),
                password=keys.get('password', ''),
                token=keys.get('token', ''),
                ble_address=keys.get('ble_address', ''),
                is_complete=required.issubset(present),
            )
        self._devices[user_id] = result

    # ==================== 缓存失效 ====================

    async def invalidate_user(self, user_id: int):
        """用户设备变更后清除缓存，下次查询时自动重载"""
        self._devices.pop(user_id, None)

    async def invalidate_platform(self, user_id: int, platform: str):
        """清除单个用户单个平台的缓存"""
        if user_id in self._devices:
            self._devices[user_id].pop(platform, None)

    async def invalidate_all(self):
        """清空全部缓存（极少使用）"""
        self._devices.clear()
        self._loaded = False

    # ==================== 统计 ====================

    def stats(self) -> Dict[str, Any]:
        total_users = len(self._devices)
        total_platforms = sum(len(p) for p in self._devices.values())
        return {
            'loaded': self._loaded,
            'users': total_users,
            'platforms': total_platforms,
        }


# 全局单例
device_cache = DeviceCacheManager()
