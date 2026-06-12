"""
PetKit设备服务 - 重构版（实现DeviceBase接口）

架构改进：
1. 继承DeviceBase抽象类，实现统一接口
2. 保持所有现有业务逻辑不变
3. 添加类型提示和文档字符串
4. 优化错误处理和日志记录
5. 支持异步上下文管理器

迁移指南：
- 旧代码：petkit_service.py（保持兼容）
- 新代码：petkit_service_refactored.py（推荐）
- 逐步迁移：先在新功能使用新接口，旧代码保持不变
"""
import os
import ssl
import asyncio
import logging
import json
import time
import re
from sqlmodel import Session, select
from backend.app.models.db import engine
from backend.app.models.models import SystemConfig
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

try:
    from pypetkitapi.client import PetKitClient
    PYPETKITAPI_AVAILABLE = True
except ImportError:
    PYPETKITAPI_AVAILABLE = False
    PetKitClient = None

from backend.app.services.device_base import DeviceBase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 常量定义
SESSION_EXPIRY_MS = 30 * 60 * 1000  # 30分钟
SESSION_REFRESH_THRESHOLD_MIN = 25  # 25分钟
DEVICE_CACHE_TTL = 30  # 设备数据缓存30秒
SUPPORTED_DEVICE_TYPES = {'T3', 'T4', 'T5'}

# 预编译正则表达式
RAW_STATE_PATTERN = re.compile(r'(\w+)=([\w\d\.\-]+)')
WIFI_PATTERN = re.compile(r'wifi=Wifi\(bssid=\'(.*?)\', rsq=(-?\d+)')


class PetKitServiceRefactored(DeviceBase):
    """
    PetKit设备服务 - 重构版
    
    实现DeviceBase接口，提供统一的设备控制能力。
    
    功能：
    - 小佩猫厕所控制（清洗、除臭）
    - 设备状态查询
    - 会话管理（自动保存/恢复）
    - SSL错误处理
    
    使用示例：
        # 方法1：使用异步上下文管理器（推荐）
        async with PetKitServiceRefactored(user_id=123) as service:
            await service.initialize({'account': 'user@example.com', 'password': 'secret'})
            devices = await service.get_data('devices')
            result = await service.execute_command('clean', {'device_id': '12345'})
        
        # 方法2：手动管理生命周期
        service = PetKitServiceRefactored(user_id=123)
        try:
            await service.initialize({'account': 'user@example.com', 'password': 'secret'})
            status = await service.get_status()
        finally:
            await service.close()
    """
    
    # ==================== DeviceBase 属性实现 ====================
    
    @property
    def platform(self) -> str:
        """设备平台标识"""
        return 'petkit'
    
    @property
    def device_type(self) -> str:
        """设备类型"""
        return 'litterbox'
    
    # ==================== 初始化 ====================
    
    def __init__(self, username=None, password=None, region="CN", timezone="Asia/Shanghai", user_id=None):
        """
        初始化PetKitService
        
        Args:
            username: PetKit账号（可选，可通过initialize()传入）
            password: PetKit密码（可选，可通过initialize()传入）
            region: 区域（默认CN）
            timezone: 时区（默认Asia/Shanghai）
            user_id: 用户ID（用于多租户隔离）
        """
        self.username = username
        self.password = password
        self.user_id = user_id
        self.region = region
        self.timezone = timezone
        self.session = None
        self.client = None
        self._devices_last_refresh = 0
        self._devices_refresh_lock = asyncio.Lock()
        self._ssl_context = None
        self._initialized = False

        # 延迟初始化，避免在 __init__ 中执行阻塞操作
        if not self.username or not self.password:
            logger.info("PetKitService created (credentials will be loaded in initialize())")

    async def initialize(self, config: Dict[str, Any]) -> bool:
        """
        初始化设备连接
        
        实现DeviceBase.initialize()接口。
        
        Args:
            config: 配置字典，包含：
                - account: PetKit账号（可选，如未传入则使用构造函数参数）
                - password: PetKit密码（可选）
                - region: 区域（可选，默认CN）
                - timezone: 时区（可选，默认Asia/Shanghai）
                
        Returns:
            bool: 初始化是否成功
            
        示例：
            >>> service = PetKitServiceRefactored(user_id=123)
            >>> success = await service.initialize({
            ...     'account': 'user@example.com',
            ...     'password': 'secret'
            ... })
        """
        # 更新配置
        if config.get('account'):
            self.username = config['account']
        if config.get('password'):
            self.password = config['password']
        if config.get('region'):
            self.region = config['region']
        if config.get('timezone'):
            self.timezone = config['timezone']
        
        # 异步获取凭证（如果未提供）
        await self._get_credentials()
        
        if not self.username or not self.password:
            logger.error("PetKit credentials not configured")
            return False
        
        # 初始化 SSL 上下文
        await self._init_ssl_context()
        
        # 尝试从DB加载会话，失败则重新登录
        if not await self._load_session_from_db():
            logger.info("No valid session found, attempting initial login...")
            success = await self._login()
            if success:
                logger.info("Initial login successful")
            else:
                logger.error("Initial login failed")
            return success
        else:
            logger.info("PetKit session loaded from DB")
            self._initialized = True
            return True
    
    # ==================== DeviceBase 接口实现 ====================
    
    async def get_status(self) -> Dict[str, Any]:
        """
        获取设备状态
        
        实现DeviceBase.get_status()接口。
        
        Returns:
            状态字典，包含：
            - online: 是否在线（始终True，PetKit设备通过API访问）
            - devices: 设备列表，每个设备包含：
                - device_id: 设备ID
                - device_name: 设备名称
                - work_state: 工作状态
                - battery: 电量百分比
                - state_summary: 详细状态摘要
                
        示例：
            >>> status = await service.get_status()
            >>> for device in status['devices']:
            ...     print(f"{device['device_name']}: {device['work_state']}")
        """
        if not self._initialized:
            await self.initialize({})
        
        devices = await self.get_devices()
        status = {
            'online': True,  # PetKit设备默认在线（通过API访问）
            'platform': self.platform,
            'devices': []
        }
        
        for device in devices:
            device_status = {
                'device_id': device.get('id'),
                'device_name': device.get('name'),
                'device_type': device.get('type'),
                'work_state': device.get('state_summary', {}).get('work_state'),
                'battery': device.get('state_summary', {}).get('battery'),
                'state_summary': device.get('state_summary', {}),
            }
            status['devices'].append(device_status)
        
        return status
    
    async def execute_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行设备控制命令
        
        实现DeviceBase.execute_command()接口。
        
        支持的命令：
        - 'clean': 清洗猫厕所
        - 'deodorize': 除臭
        
        Args:
            command: 命令名称
            params: 命令参数，包含：
                - device_id: 目标设备ID（可选，默认第一个设备）
                
        Returns:
            执行结果字典
            
        Raises:
            ValueError: 未知命令
            RuntimeError: 命令执行失败
            
        示例：
            >>> result = await service.execute_command('clean', {'device_id': '12345'})
            >>> print(result['status'])  # 'success' or 'error'
        """
        if command == 'clean':
            device_id = params.get('device_id')
            return await self.clean_litterbox(device_id)
        elif command == 'deodorize':
            device_id = params.get('device_id')
            return await self.deodorize_litterbox(device_id)
        else:
            raise ValueError(f"Unknown command: {command}. Supported: 'clean', 'deodorize'")
    
    async def get_data(self, data_type: str, **kwargs) -> Any:
        """
        获取设备数据
        
        实现DeviceBase.get_data()接口。
        
        支持的数据类型：
        - 'devices': 设备列表
        - 'status': 设备状态（别名，等同于devices）
        
        Args:
            data_type: 数据类型
            **kwargs: 额外参数（预留）
            
        Returns:
            数据对象
            
        Raises:
            ValueError: 未知数据类型
            
        示例：
            >>> devices = await service.get_data('devices')
            >>> print(len(devices))
        """
        if data_type in ('devices', 'status'):
            return await self.get_devices()
        else:
            raise ValueError(f"Unknown data_type: {data_type}. Supported: 'devices', 'status'")
    
    async def close(self):
        """
        关闭设备连接，释放资源
        
        实现DeviceBase.close()接口。
        """
        await self._close_session()
        self._initialized = False
        logger.info("PetKitService closed")
    
    # ==================== 私有方法（保持原有逻辑） ====================
    
    async def _get_credentials(self):
        """异步获取凭证，避免阻塞 __init__"""
        if not self.username or not self.password:
            from backend.app.utils.config_manager import get_config_from_db
            self.username = await get_config_from_db("account", user_id=self.user_id, platform="petkit")
            self.password = await get_config_from_db("password", user_id=self.user_id, platform="petkit")
            if self.username or self.password:
                logger.info(f"Loaded credentials from DB for user {self.user_id}")

    async def _init_ssl_context(self):
        """初始化 SSL 上下文，处理证书验证问题"""
        try:
            from backend.app.utils.config_manager import get_config_from_db
            disable_ssl_str = await get_config_from_db("PETKIT_DISABLE_SSL_VERIFY")
            disable_ssl = disable_ssl_str.lower() == "true" if disable_ssl_str else False
            
            if disable_ssl:
                logger.warning("SSL verification disabled for PetKit (development mode only)")
                self._ssl_context = False
            else:
                self._ssl_context = ssl.create_default_context()
                logger.info("SSL context created with default verification")
        except Exception as e:
            logger.error(f"Failed to create SSL context: {e}")
            self._ssl_context = False

    async def _save_disable_ssl_config(self):
        """SSL降级成功后持久化到DB，避免下次启动重试"""
        try:
            from backend.app.utils.config_manager import set_config_to_db
            await set_config_to_db("PETKIT_DISABLE_SSL_VERIFY", user_id=0, value="true", platform="petkit")
            logger.info("Persisted PETKIT_DISABLE_SSL_VERIFY=true to DB (won't retry SSL on next startup)")
        except Exception as e:
            logger.warning(f"Failed to persist SSL config: {e}")

    async def _load_session_from_db(self) -> bool:
        """Try to load the latest session data（三级缓存：Redis → DB）"""
        try:
            session_key = self._session_cache_key()
            from backend.app.utils.redis_cache import redis_cache
            
            # ---- 1. 检查 Redis 缓存 ----
            redis_data = await redis_cache.get(session_key)
            if redis_data:
                saved_time = redis_data.get('timestamp', 0)
                if int(time.time() * 1000) - saved_time <= SESSION_EXPIRY_MS:
                    logger.info("PetKit session restored from Redis cache")
                    restored = await self._restore_session(redis_data)
                    if restored:
                        self._initialized = True
                    return restored
            
            # ---- 2. 从DB加载 ----
            loop = asyncio.get_event_loop()
            
            def _load():
                with Session(engine) as session_db:
                    statement = select(SystemConfig).where(
                        SystemConfig.key == 'token',
                        SystemConfig.platform == 'petkit',
                        SystemConfig.user_id == (self.user_id or 0),
                        SystemConfig.is_active == True
                    ).order_by(SystemConfig.id.desc())
                    config = session_db.exec(statement).first()
                    if config:
                        return config.value
                return None
            
            config_value = await loop.run_in_executor(None, _load)
            
            if config_value:
                if not config_value.strip() or config_value.strip() in ('null', 'None', ''):
                    logger.warning("PetKit session data in DB is empty/invalid, need re-login")
                    return False
                
                try:
                    session_data = json.loads(config_value)
                except json.JSONDecodeError as e:
                    logger.warning(f"PetKit session JSON parse failed: {e}, will re-login")
                    return False
                
                saved_time = session_data.get('timestamp', 0)
                current_time = int(time.time() * 1000)
                
                if current_time - saved_time > SESSION_EXPIRY_MS:
                    logger.info("PetKit session expired, need re-login")
                    return False
                
                # 写入 Redis 缓存
                await redis_cache.set(session_key, session_data, ttl=1800)
                
                restored = await self._restore_session(session_data)
                if restored:
                    self._initialized = True
                return restored
        except Exception as e:
            logger.warning(f"Could not load session from DB: {e}")
        return False

    def _session_cache_key(self) -> str:
        """生成会话缓存Key"""
        return f"petkit_session:user_{self.user_id or 0}"

    async def _save_session_to_db(self):
        """Save current session data to database + Redis"""
        try:
            if not self.client or not self.session:
                return
            
            session_data: dict = {
                'timestamp': int(time.time() * 1000),
                'region': self.region,
                'timezone': self.timezone,
                'username': self.username,
                'has_valid_session': True,
            }
            
            try:
                if hasattr(self.client, 'req') and hasattr(self.client.req, 'session'):
                    raw_cookies = self.client.req.session.cookie_jar.filter_cookies()
                    if raw_cookies:
                        cookie_dict = {}
                        for cookie in raw_cookies.values():
                            cookie_dict[cookie.key] = cookie.value
                        session_data['cookies_dict'] = cookie_dict
                    
                    if hasattr(self.client.req, 'headers'):
                        auth_headers = {}
                        for key in ['authorization', 'token', 'x-auth-token', 'session-id']:
                            val = self.client.req.headers.get(key)
                            if val:
                                auth_headers[key] = val
                        if auth_headers:
                            session_data['auth_headers'] = auth_headers
            except Exception as e:
                logger.debug(f"Could not extract session details: {e}")
            
            safe_json = json.dumps(session_data, ensure_ascii=False, default=str)
            
            loop = asyncio.get_event_loop()
            
            def _save():
                with Session(engine) as session_db:
                    statement = select(SystemConfig).where(
                        SystemConfig.key == 'token',
                        SystemConfig.platform == 'petkit',
                        SystemConfig.device_name == 'petkit',
                        SystemConfig.user_id == (self.user_id or 0),
                        SystemConfig.is_active == True,
                    )
                    config = session_db.exec(statement).first()
                    
                    if not config:
                        config = SystemConfig(
                            user_id=self.user_id or 0,
                            key='token',
                            platform='petkit',
                            device_name='petkit',
                            value=safe_json,
                            is_encrypted=False,
                            is_active=True,
                        )
                        session_db.add(config)
                    else:
                        config.value = safe_json
                        config.updated_at = int(time.time() * 1000)
                        config.is_encrypted = False
                        session_db.add(config)
                    
                    session_db.commit()
            
            await loop.run_in_executor(None, _save)
            # 同步写入 Redis
            from backend.app.utils.redis_cache import redis_cache
            await redis_cache.set(self._session_cache_key(), session_data, ttl=1800)
            logger.info("Saved PetKit session to database + Redis")
        except Exception as e:
            logger.error(f"Failed to save session to DB: {e}")

    async def _restore_session(self, session_data: dict) -> bool:
        """Restore session from stored data"""
        try:
            saved_time = session_data.get('timestamp', 0)
            current_time = int(time.time() * 1000)
            age_minutes = (current_time - saved_time) / (60 * 1000)
            
            if age_minutes > SESSION_REFRESH_THRESHOLD_MIN:
                logger.info(f"Session too old ({age_minutes:.1f}min), will re-login")
                return False
            
            await self._close_session()
            
            connector = aiohttp.TCPConnector(ssl=self._ssl_context) if self._ssl_context is not None else None
            self.session = aiohttp.ClientSession(connector=connector)
            
            # 恢复 cookies
            cookie_dict = session_data.get('cookies_dict')
            if cookie_dict:
                try:
                    from aiohttp import CookieJar
                    for name, value in cookie_dict.items():
                        self.session.cookie_jar.update_cookies({name: value})
                    logger.debug(f"Restored {len(cookie_dict)} cookies to PetKit session")
                except Exception as e:
                    logger.debug(f"Cookie restore failed (non-fatal): {e}")
            
            # 恢复 auth headers
            auth_headers = session_data.get('auth_headers', {})
            for key, value in auth_headers.items():
                self.session.headers[key] = value
            
            # 恢复旧格式 cookies（兼容性）
            old_cookies_str = session_data.get('cookies')
            if old_cookies_str and not cookie_dict:
                try:
                    from http.cookies import SimpleCookie
                    c = SimpleCookie()
                    c.load(old_cookies_str)
                    for morsel in c.values():
                        self.session.cookie_jar.update_cookies({morsel.key: morsel.value})
                    logger.debug("Restored cookies from legacy string format")
                except Exception as e:
                    logger.debug(f"Legacy cookie restore failed: {e}")
            
            self.client = PetKitClient(
                username=self.username,
                password=self.password,
                region=session_data.get('region', self.region),
                timezone=session_data.get('timezone', self.timezone),
                session=self.session,
            )
            
            # 验证会话是否有效
            try:
                await self.client.get_devices_data()
                logger.info(f"PetKit session restored successfully. Found {len(self.client.petkit_entities)} devices.")
                self._devices_last_refresh = time.time()
                return True
            except Exception as e:
                logger.warning(f"Restored session invalid, need re-login: {e}")
                await self._close_session()
                return False
                
        except Exception as e:
            logger.error(f"Failed to restore session: {e}")
            await self._close_session()
            return False

    async def _close_session(self):
        """安全关闭会话"""
        if self.session:
            try:
                await self.session.close()
            except Exception as e:
                logger.debug(f"Error closing session: {e}")
            finally:
                self.session = None
                self.client = None
                self._initialized = False

    async def _login(self) -> bool:
        """Login to get new session"""
        try:
            await self._close_session()
            
            connector = aiohttp.TCPConnector(ssl=self._ssl_context) if self._ssl_context is not None else None
            self.session = aiohttp.ClientSession(connector=connector)
            
            self.client = PetKitClient(
                username=self.username,
                password=self.password,
                region=self.region,
                timezone=self.timezone,
                session=self.session,
            )
            
            await self.client.get_devices_data()
            logger.info(f"PetKit login successful. Found {len(self.client.petkit_entities)} devices.")
            self._devices_last_refresh = time.time()
            
            await self._save_session_to_db()
            self._initialized = True
            return True
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"PetKit login failed: {e}")
            
            # 如果是 SSL 证书错误，尝试禁用 SSL 验证重试
            if "SSL" in error_msg or "certificate" in error_msg.lower() or "CERTIFICATE_VERIFY_FAILED" in error_msg:
                logger.warning("SSL certificate error detected, retrying with SSL verification disabled...")
                try:
                    await self._close_session()
                    
                    connector = aiohttp.TCPConnector(ssl=False)
                    self.session = aiohttp.ClientSession(connector=connector)
                    self.client = PetKitClient(
                        username=self.username,
                        password=self.password,
                        region=self.region,
                        timezone=self.timezone,
                        session=self.session,
                    )
                    
                    await self.client.get_devices_data()
                    logger.warning("PetKit login successful (SSL verification disabled - development only)")
                    self._devices_last_refresh = time.time()
                    # SSL降级成功后持久化到DB，下次启动不再重试
                    await self._save_disable_ssl_config()
                    await self._save_session_to_db()
                    self._initialized = True
                    return True
                except Exception as retry_error:
                    logger.error(f"Retry with SSL disabled failed: {retry_error}")
            
            return False

    async def _refresh_devices(self):
        """刷新设备数据"""
        try:
            await self.client.get_devices_data()
            self._devices_last_refresh = time.time()
            await self._save_session_to_db()
        except Exception as e:
            error_msg = str(e)
            is_ssl_error = "SSL" in error_msg or "certificate" in error_msg.lower() or "CERTIFICATE_VERIFY_FAILED" in error_msg
            
            if "Session expired" in error_msg or "401" in error_msg or is_ssl_error:
                logger.warning("Session expired, attempting re-login...")
                if await self._login():
                    await self.client.get_devices_data()
                    self._devices_last_refresh = time.time()
                else:
                    raise Exception("Re-login failed")
            else:
                raise e

    async def _refresh_devices_if_needed(self):
        """根据缓存时间刷新设备数据"""
        current_time = time.time()
        if current_time - self._devices_last_refresh > DEVICE_CACHE_TTL:
            async with self._devices_refresh_lock:
                # 双重检查
                if time.time() - self._devices_last_refresh > DEVICE_CACHE_TTL:
                    await self._refresh_devices()

    def _get_device_type(self, entity) -> str:
        """获取设备类型"""
        target_type = 'Unknown'
        if hasattr(entity, 'device_nfo') and hasattr(entity.device_nfo, 'device_type'):
            target_type = entity.device_nfo.device_type.upper()
        else:
            target_type = getattr(entity, 'device_type', '').upper()
        
        if target_type == 'UNKNOWN' and hasattr(entity, 'name'):
            name = entity.name
            if 'MAX' in name or '猫厕所' in name:
                target_type = 'T4'
        
        return target_type

    def _is_supported_device(self, entity) -> bool:
        """检查是否为支持的设备类型"""
        dev_type = self._get_device_type(entity)
        return dev_type in SUPPORTED_DEVICE_TYPES

    def _extract_info_from_raw_state(self, raw_state: str, state_summary: dict):
        """从原始状态字符串中提取关键信息"""
        key_fields = [
            'deodorant_left_days', 'sand_percent', 'sand_weight',
            'used_times', 'frequent_restroom', 'liquid_lack',
            'box_full', 'sand_lack', 'power', 'ota'
        ]
        
        for match in RAW_STATE_PATTERN.finditer(raw_state):
            field = match.group(1)
            value = match.group(2)
                
            if field in key_fields:
                try:
                    if field in ('deodorant_left_days', 'sand_percent', 'used_times'):
                        state_summary[field] = int(value)
                    elif field in ('sand_weight',):
                        state_summary[field] = float(value)
                    else:
                        state_summary[field] = value
                except ValueError:
                    state_summary[field] = value
        
        # 提取WiFi信息
        wifi_match = WIFI_PATTERN.search(raw_state)
        if wifi_match:
            state_summary['wifi_bssid'] = wifi_match.group(1)
            state_summary['wifi_rssi'] = int(wifi_match.group(2))

    async def get_devices(self):
        """Get all devices（保持原有接口兼容）"""
        if not self.client:
            await self.initialize({})
        
        await self._refresh_devices_if_needed()
        
        devices = []
        for dev_id, entity in self.client.petkit_entities.items():
            if hasattr(entity, 'pet_id'):
                continue
            
            dev_type = self._get_device_type(entity)
            if dev_type not in SUPPORTED_DEVICE_TYPES:
                continue
            
            logger.info(f"Processing device: {getattr(entity, 'name', 'Unknown')} (Type: {dev_type}, ID: {entity.id})")
            
            dev_data = {
                "id": str(entity.id),
                "name": getattr(entity, 'name', 'Unknown'),
                "type": dev_type,
                "data": {}
            }
            
            if hasattr(entity, 'data') and entity.data:
                try:
                    raw_data = entity.data
                    if isinstance(raw_data, dict):
                        dev_data["data"] = {k: v for k, v in raw_data.items() if isinstance(v, (str, int, float, bool, type(None)))}
                    else:
                        dev_data["data"] = str(raw_data)
                except Exception:
                    dev_data["data"] = {}
            
            state_summary = {}
            if hasattr(entity, 'state'):
                state_obj = entity.state
                known_state_attrs = ['box_full', 'liquid_lack', 'box_state', 'work_state', 'error_state']
                for sattr in known_state_attrs:
                    if hasattr(state_obj, sattr):
                        state_summary[sattr] = getattr(state_obj, sattr)
                
                raw_state_str = str(state_obj)
                state_summary['raw_state'] = raw_state_str
                self._extract_info_from_raw_state(raw_state_str, state_summary)
            
            interesting_attrs = [
                'liquid', 'weight', 'times', 'battery', 'connection',
                'sand_percent', 'deodorant_left_days', 'used_times'
            ]
            for attr in interesting_attrs:
                val = None
                if hasattr(entity, attr):
                    val = getattr(entity, attr)
                elif hasattr(entity, 'data') and isinstance(entity.data, dict) and attr in entity.data:
                    val = entity.data[attr]
                
                if val is not None and isinstance(val, (str, int, float, bool)):
                    state_summary[attr] = val
                elif val is not None:
                    state_summary[attr] = str(val)
            
            if hasattr(entity, 'device_stats'):
                device_stats = entity.device_stats
                # 优先使用今日统计数据，如果没有则使用累计数据并标记警告
                today_times = getattr(device_stats, 'times', 0)
                state_summary['today_visits'] = today_times
                state_summary['avg_duration'] = getattr(device_stats, 'avg_time', 0)
                state_summary['total_duration'] = getattr(device_stats, 'total_time', 0)
                
                # 检查是否有更详细的统计信息来验证今日数据
                if hasattr(device_stats, 'statistic_info') and device_stats.statistic_info:
                    stat_info = device_stats.statistic_info
                    if stat_info and len(stat_info) > 0:
                        latest_record = stat_info[-1]
                        latest_weight = getattr(latest_record, 'pet_weight', 0)
                        if latest_weight > 0:
                            state_summary['last_pet_weight'] = latest_weight / 1000.0
            
            dev_data["state_summary"] = state_summary
            devices.append(dev_data)
        return devices

    async def clean_litterbox(self, device_id=None):
        """Trigger clean action for the first found or specified litterbox"""
        if not self.client:
            await self.initialize({})
        
        target_id = None
        if not device_id:
            for dev_id, entity in self.client.petkit_entities.items():
                if self._is_supported_device(entity):
                    target_id = dev_id
                    break
        else:
            target_id = int(device_id) if str(device_id).isdigit() else device_id
        
        if not target_id:
            raise Exception("No litterbox found or invalid device ID")
        
        logger.info(f"Sending clean command to {target_id}")
        try:
            from pypetkitapi.command import DeviceCommand, DeviceAction, LBCommand
            await self.client.send_api_request(
                target_id,
                DeviceCommand.CONTROL_DEVICE,
                {DeviceAction.START: LBCommand.CLEANING}
            )
            await self._save_session_to_db()
            return {"status": "success", "device_id": str(target_id), "action": "clean"}
        except Exception as e:
            if "Session expired" in str(e) or "401" in str(e):
                logger.warning("Session expired during clean, re-logging in...")
                if await self._login():
                    await self.client.send_api_request(
                        target_id,
                        DeviceCommand.CONTROL_DEVICE,
                        {DeviceAction.START: LBCommand.CLEANING}
                    )
                    await self._save_session_to_db()
                    return {"status": "success", "device_id": str(target_id), "action": "clean"}
                else:
                    raise Exception("Re-login failed")
            raise e

    async def deodorize_litterbox(self, device_id=None):
        """Trigger deodorize (spray) for the first found or specified litterbox"""
        if not self.client:
            await self.initialize({})
        
        target_id = None
        if not device_id:
            for dev_id, entity in self.client.petkit_entities.items():
                if self._is_supported_device(entity):
                    target_id = dev_id
                    break
        else:
            target_id = int(device_id) if str(device_id).isdigit() else device_id
        
        if not target_id:
            raise Exception("No litterbox found or invalid device ID")
        
        logger.info(f"Sending deodorize command to {target_id}")
        try:
            from pypetkitapi.command import LitterCommand, DeviceAction, LBCommand
            await self.client.send_api_request(
                target_id,
                LitterCommand.CONTROL_DEVICE,
                {DeviceAction.START: LBCommand.DEODORIZE}
            )
            await self._save_session_to_db()
            return {"status": "success", "device_id": str(target_id), "action": "deodorize"}
        except Exception as e:
            if "Session expired" in str(e) or "401" in str(e):
                logger.warning("Session expired during deodorize, re-logging in...")
                if await self._login():
                    await self.client.send_api_request(
                        target_id,
                        LitterCommand.CONTROL_DEVICE,
                        {DeviceAction.START: LBCommand.DEODORIZE}
                    )
                    await self._save_session_to_db()
                    return {"status": "success", "device_id": str(target_id), "action": "deodorize"}
                else:
                    raise Exception("Re-login failed")
            raise e


# 导出别名，保持向后兼容
PetKitService = PetKitServiceRefactored
