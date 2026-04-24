from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from sqlmodel import select

try:
    from pypetkitapi.client import PetKitClient
    from pypetkitapi.command import DeviceCommand, DeviceAction, LBCommand, LitterCommand
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

from ..models.db import Session
from ..models.models import SystemConfig
from ..utils.config_manager import get_config_from_db, decrypt_value, encrypt_value, ConfigManager, ConfigEncryptor
from .demo_data import petkit_devices

logger = logging.getLogger(__name__)


class PetKitService:
    def __init__(self) -> None:
        self._client: Optional[PetKitClient] = None
        self._devices_cache: list[dict] = []
        self._session_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._sdk_available = SDK_AVAILABLE
        self._initialized = False
        
        # 如果没有SDK,使用演示数据
        if not self._sdk_available:
            logger.warning("⚠ pypetkitapi SDK 未安装,使用演示数据")
            self._devices_cache = petkit_devices()

    async def initialize(self, user_id: int, session: Session) -> bool:
        """初始化 PetKit 服务,从数据库加载配置并登录"""
        if not self._sdk_available:
            logger.warning("⚠ PetKit SDK 不可用,跳过初始化")
            return False
        
        try:
            # 从数据库读取账号密码
            account_encrypted = get_config_from_db(session, user_id, "account", "petkit")
            password_encrypted = get_config_from_db(session, user_id, "password", "petkit")
            
            if not account_encrypted or not password_encrypted:
                logger.warning(f"⚠ PetKit 配置不完整 (user_id={user_id})")
                return False
            
            # 解密账号密码
            account = decrypt_value(account_encrypted)
            password = decrypt_value(password_encrypted)
            
            # 检查是否已有有效会话
            if self._is_session_valid():
                logger.info("✓ PetKit 会话仍然有效")
                await self._refresh_devices()
                return True
            
            # 尝试从数据库加载缓存的会话
            cached_session = get_config_from_db(session, user_id, "session_token", "petkit")
            if cached_session:
                self._session_token = decrypt_value(cached_session)
                expires_str = get_config_from_db(session, user_id, "session_expires", "petkit")
                if expires_str:
                    try:
                        self._token_expires_at = datetime.fromisoformat(decrypt_value(expires_str))
                    except Exception:
                        self._token_expires_at = None
            
            # 登录
            success = await self._login(account, password)
            if success:
                # 保存会话到数据库
                if self._session_token:
                    config_mgr = ConfigManager(ConfigEncryptor())
                    config_mgr.upsert_config(
                        session, user_id=user_id, key="session_token",
                        value=encrypt_value(self._session_token),
                        platform="petkit", is_encrypted=True
                    )
                    if self._token_expires_at:
                        config_mgr.upsert_config(
                            session, user_id=user_id, key="session_expires",
                            value=encrypt_value(self._token_expires_at.isoformat()),
                            platform="petkit", is_encrypted=True
                        )
                    session.commit()
                
                await self._refresh_devices()
                logger.info(f"✓ PetKit 自动登录成功,发现 {len(self._devices_cache)} 个设备")
                return True
            else:
                logger.warning(f"⚠ PetKit 自动登录失败 (user_id={user_id})")
                return False
                
        except Exception as e:
            logger.error(f"❌ PetKit 初始化异常: {e}", exc_info=True)
            return False

    async def _login(self, account: str, password: str) -> bool:
        """登录 PetKit 云平台"""
        try:
            import os
            import ssl
            
            # 处理手机号格式 (去掉 86- 前缀)
            phone = account.replace("86-", "") if account.startswith("86-") else account
            
            # 创建 aiohttp session,禁用 SSL 验证(开发环境)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            http_session = aiohttp.ClientSession(connector=connector)
            
            # 创建 PetKit 客户端
            self._client = PetKitClient(
                username=f"86-{phone}",
                password=password,
                region="CN",
                timezone="Asia/Shanghai",
                session=http_session
            )
            
            # 登录并获取设备数据
            await self._client.get_devices_data()
            
            # 保存会话信息
            self._session_token = getattr(self._client, 'session_token', None)
            self._token_expires_at = datetime.now() + timedelta(minutes=30)  # 30分钟有效期
            self._initialized = True
            
            logger.info("✓ PetKit 登录成功")
            return True
            
        except Exception as e:
            logger.error(f"❌ PetKit 登录失败: {e}")
            if self._client and hasattr(self._client, 'session'):
                await self._client.session.close()
            self._client = None
            return False

    def _is_session_valid(self) -> bool:
        """检查会话是否仍然有效"""
        if not self._client or not self._initialized:
            return False
        if not self._token_expires_at:
            return False
        return datetime.now() < self._token_expires_at

    async def _refresh_devices(self):
        """刷新设备列表"""
        if not self._client:
            return
        
        try:
            devices_dict = self._client.petkit_entities
            self._devices_cache = []
            
            for device_id, entity in devices_dict.items():
                # 过滤掉宠物档案等非设备实体
                if hasattr(entity, 'pet_id') and entity.pet_id:
                    continue
                
                # 识别设备类型
                device_type = getattr(entity.device_nfo, 'device_type', '') if hasattr(entity, 'device_nfo') else ''
                
                # 只处理设备,不处理其他实体
                if device_type in ['T3', 'T4', 'T5']:  # 猫厕所型号
                    device_stats = getattr(entity, 'device_stats', None)
                    
                    today_visits = 0
                    avg_duration = "0m"
                    last_pet_weight = 0
                    
                    if device_stats:
                        today_visits = getattr(device_stats, 'times', 0)
                        avg_time = getattr(device_stats, 'avg_time', 0)
                        avg_duration = f"{avg_time // 60}m {avg_time % 60}s" if avg_time else "0m"
                        
                        # 获取最新体重
                        statistic_info = getattr(device_stats, 'statistic_info', [])
                        if statistic_info:
                            last_pet_weight = statistic_info[-1].pet_weight / 1000.0  # 转换为kg
                    
                    device_info = {
                        "id": device_id,
                        "name": getattr(entity.device_nfo, 'name', f'设备 {device_id}') if hasattr(entity, 'device_nfo') else f'设备 {device_id}',
                        "model": f"PetKit {device_type}" if device_type else "PetKit Device",
                        "status": "online",
                        "sand_level": getattr(entity, 'sand_percent', 70),
                        "odor_level": "低",
                        "last_cleaned_at": datetime.now().strftime("%H:%M"),
                        "stats": {
                            "today_visits": today_visits,
                            "avg_duration": avg_duration,
                            "last_pet_weight": round(last_pet_weight, 2)
                        }
                    }
                    self._devices_cache.append(device_info)
            
            logger.info(f"✓ 刷新设备列表: {len(self._devices_cache)} 个设备")
            
        except Exception as e:
            logger.error(f"❌ 刷新设备列表失败: {e}")
            # 如果会话过期,标记为无效
            if "401" in str(e) or "Session expired" in str(e):
                self._token_expires_at = None

    def register_device(self, device: dict) -> None:
        """注册设备(用于手动添加的设备)"""
        if any(item["id"] == device["id"] for item in self._devices_cache):
            return
        self._devices_cache.append(
            {
                "id": device["id"],
                "name": device.get("name", device["id"]),
                "model": device.get("model", "PetKit Device"),
                "status": device.get("status", "online"),
                "sand_level": device.get("sand_level", 50),
                "odor_level": device.get("odor_level", "低"),
                "last_cleaned_at": device.get("last_cleaned_at", "00:00"),
                "stats": device.get(
                    "stats",
                    {"today_visits": 0, "avg_duration": "0m", "last_pet_weight": 0},
                ),
            }
        )

    async def list_devices(self) -> list[dict]:
        """获取设备列表"""
        if self._sdk_available and self._initialized:
            # 尝试刷新设备状态
            try:
                await self._refresh_devices()
            except Exception as e:
                logger.warning(f"刷新设备状态失败: {e}")
        return self._devices_cache

    async def stats(self) -> list[dict]:
        """获取设备统计数据"""
        return [{"id": device["id"], **device["stats"]} for device in self._devices_cache]

    async def clean(self, device_id: str) -> dict:
        """清理猫砂盆"""
        device = self._find_device(device_id)
        
        if self._sdk_available and self._client and self._initialized:
            try:
                # 发送清理指令
                await self._client.send_api_request(
                    device_id,
                    DeviceCommand.CONTROL_DEVICE,
                    {DeviceAction.START: LBCommand.CLEANING}
                )
                logger.info(f"✓ 已发送清理指令: {device_id}")
            except Exception as e:
                logger.error(f"❌ 清理指令发送失败: {e}")
                # 如果是会话过期,尝试重新登录
                if "401" in str(e) or "Session expired" in str(e):
                    await self._handle_session_expired()
        
        # 更新本地缓存
        device["last_cleaned_at"] = datetime.now().strftime("%H:%M")
        device["odor_level"] = "低"
        device["status"] = "online"
        
        return {
            "ok": True,
            "device_id": device_id,
            "action": "clean",
            "message": f"{device['name']} 已完成清理",
            "device": device,
        }

    async def deodorize(self, device_id: str) -> dict:
        """除臭"""
        device = self._find_device(device_id)
        
        if self._sdk_available and self._client and self._initialized:
            try:
                # 发送除臭指令
                await self._client.send_api_request(
                    device_id,
                    LitterCommand.CONTROL_DEVICE,
                    {DeviceAction.START: LBCommand.DESODORIZE}
                )
                logger.info(f"✓ 已发送除臭指令: {device_id}")
            except Exception as e:
                logger.error(f"❌ 除臭指令发送失败: {e}")
                if "401" in str(e) or "Session expired" in str(e):
                    await self._handle_session_expired()
        
        # 更新本地缓存
        device["odor_level"] = "低"
        
        return {
            "ok": True,
            "device_id": device_id,
            "action": "deodorize",
            "message": f"{device['name']} 已发送除臭指令",
            "device": device,
        }

    def _find_device(self, device_id: str) -> dict:
        """查找设备"""
        for device in self._devices_cache:
            if device["id"] == device_id:
                return device
        raise KeyError(device_id)

    async def _handle_session_expired(self):
        """处理会话过期,尝试重新登录"""
        logger.warning("⚠ PetKit 会话过期,尝试重新登录...")
        self._token_expires_at = None
        self._initialized = False
        # 注意:这里需要从数据库重新读取账号密码
        # 实际使用时需要在调用处传入 session 和 user_id

    async def close(self):
        """关闭服务,清理资源"""
        if self._client and hasattr(self._client, 'session'):
            await self._client.session.close()
            logger.info("✓ PetKit 客户端已关闭")
