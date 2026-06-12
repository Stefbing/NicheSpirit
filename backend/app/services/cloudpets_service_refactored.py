"""
CloudPets设备服务 - 重构版（实现DeviceBase接口）

架构改进：
1. 继承DeviceBase抽象类，实现统一接口
2. 保持所有现有业务逻辑不变
3. 添加类型提示和完整的文档字符串
4. 优化错误处理和日志记录
5. 支持异步上下文管理器

设备类型：云宠喂食机（Feeder）

使用示例：
    # 方法1：使用异步上下文管理器（推荐）
    async with CloudPetsServiceRefactored(user_id=123) as service:
        await service.initialize({
            'account': '13800138000',
            'password': 'secret'
        })
        status = await service.get_status()
        result = await service.execute_command('feed', {'amount': 1})
    
    # 方法2：手动管理生命周期
    service = CloudPetsServiceRefactored(user_id=123)
    try:
        await service.initialize({'account': 'user', 'password': 'pass'})
        plans = await service.get_data('plans')
    finally:
        await service.close()
"""
import os
import httpx
import logging
import asyncio
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from sqlmodel import Session, select
from backend.app.models.db import engine
from backend.app.models.models import SystemConfig
import time

from backend.app.services.device_base import DeviceBase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 常量定义
DEFAULT_BASE_URL = "https://cn.cloudpets.net"
DEFAULT_DEVICE_ID = "336704"
DEFAULT_DEVICE_TYPE = "66"
DEFAULT_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7]
LOGIN_MAX_RETRIES = 3
REQUEST_TIMEOUT = 10.0
COMMAND_PROCESS_DELAY = 1.0

# 延迟导入，避免模块加载时的依赖问题
from backend.app.utils.config_manager import get_config_from_db

DEFAULT_HEADERS = {
    "lang": "zh_CN",
    "platform": "Android",
    "x-cp-client": "1",
    "Content-Type": "application/x-www-form-urlencoded"
}


class FeedingPlan(BaseModel):
    """
    喂食计划数据模型
    
    属性：
        id: 计划ID（可选，新增时为None）
        time: 喂食时间（HH:mm格式）
        amount: 喂食份数
        enabled: 是否启用
        weekdays: 星期几（[1,2,3,4,5,6,7]）
        remark: 备注（可选）
    """
    id: Optional[str] = None
    time: str  # HH:mm
    amount: int  # servings (对应 serving)
    enabled: bool = True  # (对应 enable)
    weekdays: Optional[List[int]] = None  # [1,2,3,4,5,6,7] (对应 daysOfWeek)
    remark: Optional[str] = ""


class CloudPetsServiceRefactored(DeviceBase):
    """
    CloudPets设备服务 - 重构版
    
    实现DeviceBase接口，提供统一的设备控制能力。
    
    功能：
    - 云宠喂食机控制（手动喂食）
    - 喂食计划管理（CRUD）
    - 设备状态查询
    - Token管理（自动保存/恢复）
    - 自动重试和错误处理
    
    设备类型：Feeder（喂食机）
    平台标识：cloudpets
    """
    
    # ==================== DeviceBase 属性实现 ====================
    
    @property
    def platform(self) -> str:
        """设备平台标识"""
        return 'cloudpets'
    
    @property
    def device_type(self) -> str:
        """设备类型"""
        return 'feeder'
    
    # ==================== 初始化 ====================
    
    def __init__(self, user_id: Optional[int] = None):
        """
        初始化CloudPetsService
        
        Args:
            user_id: 用户ID（用于多租户隔离）
        """
        self.user_id = user_id
        self.account = None  # 保存账号密码用于重试登录
        self.password = None
        self._client = None  # 延迟初始化客户端
        self._base_url = None
        self._device_id = None
        
        logger.info(f"CloudPetsServiceRefactored created (user_id={user_id})")
    
    @property
    def client(self) -> httpx.AsyncClient:
        """
        懒加载 httpx 客户端
        
        Returns:
            httpx.AsyncClient实例
            
        Raises:
            RuntimeError: 客户端未初始化
        """
        if self._client is None:
            raise RuntimeError("CloudPetsService not initialized. Call initialize() first.")
        return self._client
    
    @property
    def device_id(self) -> str:
        """获取设备ID"""
        return self._device_id or DEFAULT_DEVICE_ID
    
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """
        初始化设备连接
        
        实现DeviceBase.initialize()接口。
        
        Args:
            config: 配置字典，包含：
                - account: CloudPets账号（手机号）
                - password: 密码
                - device_id: 设备ID（可选，默认336704）
                - base_url: API基础URL（可选）
                
        Returns:
            bool: 初始化是否成功
            
        示例：
            >>> service = CloudPetsServiceRefactored(user_id=123)
            >>> success = await service.initialize({
            ...     'account': '13800138000',
            ...     'password': 'secret'
            ... })
        """
        logger.info(f"Initializing CloudPets Service (user_id={self.user_id})...")
        
        # 更新配置
        if config.get('account'):
            self.account = self._normalize_account(config['account'])
        if config.get('password'):
            self.password = config['password']
        if config.get('device_id'):
            self._device_id = config['device_id']
        if config.get('base_url'):
            self._base_url = config['base_url']
        
        # 确保客户端已初始化
        await self._ensure_client()
        
        # 尝试从DB加载token
        if await self._load_token_from_db():
            logger.info("CloudPets token loaded from DB")
            # 保存凭据用于重试登录
            if self.account and self.password:
                logger.info("Credentials saved for re-login")
            return True
        
        # 凭据可用，尝试登录
        if not self.account or not self.password:
            logger.error("CloudPets credentials not available")
            return False
        
        logger.info(f"No token found in DB, attempting initial login...")
        if await self._login(self.account, self.password):
            logger.info("Initial login successful")
            return True
        else:
            logger.error("Initial login failed")
            return False
    
    # ==================== DeviceBase 接口实现 ====================
    
    async def get_status(self) -> Dict[str, Any]:
        """
        获取设备状态
        
        实现DeviceBase.get_status()接口。
        
        返回：
            - online: 是否在线（通过API可访问即在线）
            - platform: 平台标识
            - device_id: 设备ID
            - servings_today: 今日已喂食份数
            - plans_count: 喂食计划数量
                
        示例：
            >>> status = await service.get_status()
            >>> print(f"今日已喂食：{status['servings_today']}份")
        """
        try:
            # 获取今日喂食记录
            servings_data = await self.get_servings_today()
            servings_today = 0
            if isinstance(servings_data, dict):
                if 'result' in servings_data:
                    servings_today = len(servings_data['result']) if isinstance(servings_data['result'], list) else 0
            
            # 获取喂食计划
            plans = await self.get_feeding_plans()
            
            return {
                'online': True,  # CloudPets设备通过API访问，默认可用
                'platform': self.platform,
                'device_id': self.device_id,
                'device_type': self.device_type,
                'servings_today': servings_today,
                'plans_count': len(plans),
                'plans': plans[:3],  # 返回前3个计划摘要
            }
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return {
                'online': False,
                'platform': self.platform,
                'device_id': self.device_id,
                'error': str(e)
            }
    
    async def execute_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行设备控制命令
        
        实现DeviceBase.execute_command()接口。
        
        支持的命令：
        - 'feed': 手动喂食
            - params: {'amount': 1}  # 喂食份数
                
        Args:
            command: 命令名称
            params: 命令参数
                
        Returns:
            执行结果字典
            
        Raises:
            ValueError: 未知命令或参数无效
            
        示例：
            >>> result = await service.execute_command('feed', {'amount': 2})
            >>> print(result['status'])
        """
        if command == 'feed':
            amount = params.get('amount', 1)
            return await self.manual_feed(amount)
        else:
            raise ValueError(f"Unknown command: {command}. Supported: 'feed'")
    
    async def get_data(self, data_type: str, **kwargs) -> Any:
        """
        获取设备数据
        
        实现DeviceBase.get_data()接口。
        
        支持的数据类型：
        - 'servings': 今日喂食记录
        - 'plans': 喂食计划列表
                
        Args:
            data_type: 数据类型
            **kwargs: 额外参数
                
        Returns:
            数据对象
            
        Raises:
            ValueError: 未知数据类型
            
        示例：
            >>> servings = await service.get_data('servings')
            >>> plans = await service.get_data('plans')
        """
        if data_type == 'servings':
            return await self.get_servings_today()
        elif data_type == 'plans':
            return await self.get_feeding_plans()
        else:
            raise ValueError(f"Unknown data_type: {data_type}. Supported: 'servings', 'plans'")
    
    async def close(self):
        """
        关闭设备连接，释放资源
        
        实现DeviceBase.close()接口。
        """
        if self._client:
            try:
                await self._client.aclose()
                logger.info("CloudPetsService client closed")
            except Exception as e:
                logger.warning(f"Error closing client: {e}")
            finally:
                self._client = None
    
    # ==================== 私有方法（保持原有逻辑） ====================
    
    async def _ensure_client(self):
        """确保客户端已初始化"""
        if self._client is None:
            self._base_url = self._base_url or DEFAULT_BASE_URL
            self._device_id = self._device_id or DEFAULT_DEVICE_ID
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=DEFAULT_HEADERS.copy(),
                timeout=REQUEST_TIMEOUT
            )
            logger.info(f"HTTPX client created (base_url={self._base_url})")
    
    async def _get_config(self, key: str, user_id: Optional[int] = None) -> Optional[str]:
        """异步获取配置项"""
        uid = user_id or self.user_id
        try:
            config_value = await get_config_from_db(key, user_id=uid, platform="cloudpets")
            return config_value
        except Exception as e:
            logger.warning(f"Failed to get config {key}: {e}")
            return None
    
    @staticmethod
    def _normalize_account(account: str) -> str:
        """标准化账号格式，去除国家代码前缀"""
        if not account:
            return account
        if account.startswith("86-"):
            return account[3:]
        elif account.startswith("+86"):
            return account[3:]
        return account
    
    async def _load_token_from_db(self) -> bool:
        """Try to load the latest token from database（三级缓存：Redis → DB）"""
        try:
            token_key = self._token_cache_key()
            from backend.app.utils.redis_cache import redis_cache
            
            # ---- 1. 检查 Redis 缓存 ----
            redis_token = await redis_cache.get(token_key)
            if redis_token:
                self.client.headers["authorization"] = redis_token
                logger.info("Loaded CloudPets token from Redis cache")
                return True
            
            # ---- 2. 从DB加载 ----
            loop = asyncio.get_event_loop()
            
            def _load_token():
                with Session(engine) as session:
                    statement = select(SystemConfig).where(
                        SystemConfig.key == "token",
                        SystemConfig.platform == "cloudpets",
                        SystemConfig.user_id == (self.user_id or 0),
                        SystemConfig.is_active == True
                    ).order_by(SystemConfig.id.desc())
                    config = session.exec(statement).first()
                    if config:
                        return config.value
                return None
            
            token = await loop.run_in_executor(None, _load_token)
            if token:
                self.client.headers["authorization"] = token
                await redis_cache.set(token_key, token, ttl=1800)
                logger.info("Loaded CloudPets token from database → Redis")
                return True
        except Exception as e:
            logger.warning(f"Could not load token from DB (might be first run): {e}")
        return False
    
    def _token_cache_key(self) -> str:
        """生成token缓存Key"""
        return f"cloudpets_token:user_{self.user_id or 0}"
    
    async def _save_token_to_db(self, token: str):
        """Save new token to database"""
        try:
            loop = asyncio.get_event_loop()
            
            def _save_token():
                with Session(engine) as session:
                    # 查标准化 key='token' + platform='cloudpets'
                    statement = select(SystemConfig).where(
                        SystemConfig.key == "token",
                        SystemConfig.platform == "cloudpets",
                        SystemConfig.device_name == "cloudpets",
                        SystemConfig.user_id == (self.user_id or 0),
                        SystemConfig.is_active == True
                    )
                    config = session.exec(statement).first()
                    
                    if not config:
                        config = SystemConfig(
                            user_id=self.user_id or 0,
                            key="token",
                            platform="cloudpets",
                            device_name="cloudpets",
                            value=token,
                            is_encrypted=False,
                            is_active=True,
                            updated_at=int(time.time() * 1000),
                        )
                        session.add(config)
                    else:
                        config.value = token
                        config.updated_at = int(time.time() * 1000)
                        config.is_encrypted = False
                        session.add(config)
                    
                    session.commit()
            
            await loop.run_in_executor(None, _save_token)
            # 同步写入 Redis
            from backend.app.utils.redis_cache import redis_cache
            await redis_cache.set(self._token_cache_key(), token, ttl=1800)
            logger.info("Saved new CloudPets token to database + Redis")
        except Exception as e:
            logger.error(f"Failed to save token to DB: {e}")
    
    async def _login(self, account: str, password: str, retry_count: int = 0) -> bool:
        """
        登录获取token
        
        Path: /app/terminal/user/login
        Method: POST
        """
        try:
            logger.info(f"Attempting to login to CloudPets with account {account}")
            payload = {
                "account": account,
                "pwd": password,
                "userType": "1"
            }
            # Login endpoint needs clean headers without old auth
            login_headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "lang": "zh_CN",
                "platform": "Android",
                "x-cp-client": "1"
            }
            # 移除旧的 authorization header，避免干扰登录请求
            had_auth = "authorization" in self.client.headers
            old_auth = self.client.headers.pop("authorization", None)
            logger.debug(f"Temporarily removed authorization header for login (had_auth={had_auth})")
            
            resp = await self.client.post("/app/terminal/user/login", data=payload, headers=login_headers)
            resp.raise_for_status()
            data = resp.json()
            
            new_token = None
            if "authorization" in data:
                new_token = data["authorization"]
            elif "result" in data:
                if isinstance(data["result"], dict) and "authorization" in data["result"]:
                    new_token = data["result"]["authorization"]
                elif isinstance(data["result"], str):
                    # Sometimes result IS the token
                    new_token = data["result"]
            
            # Sometimes it's just in the header of the response
            if not new_token and "authorization" in resp.headers:
                new_token = resp.headers["authorization"]
            
            if new_token:
                self.client.headers["authorization"] = new_token
                await self._save_token_to_db(new_token)
                return True
            else:
                logger.error(f"Could not find token in login response: {data}")
                return False
                
        except httpx.HTTPStatusError as e:
            # HTTP 错误，尝试重试
            if retry_count < LOGIN_MAX_RETRIES:
                logger.warning(f"Login failed with status {e.response.status_code}, retrying ({retry_count + 1}/{LOGIN_MAX_RETRIES})...")
                await asyncio.sleep(1 * (retry_count + 1))  # 指数退避
                return await self._login(account, password, retry_count + 1)
            logger.error(f"Login failed after {LOGIN_MAX_RETRIES} retries: {e}")
            return False
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False
    
    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Wrapper for HTTP requests with auto-login on 401 or specific business errors
        """
        max_retries = 2
        for attempt in range(max_retries):
            try:
                resp = await self.client.request(method, url, **kwargs)
                
                # Check for HTTP 401
                should_retry = resp.status_code == 401
                
                # Also check for business logic 401
                if not should_retry and resp.status_code == 200:
                    try:
                        data = resp.json()
                        if isinstance(data, dict) and str(data.get("code")) == "401":
                            should_retry = True
                            logger.warning(f"Detected business logic 401: {data}")
                    except Exception:
                        pass
                
                if should_retry and attempt < max_retries - 1:
                    logger.warning(f"Received 401 from CloudPets (attempt {attempt + 1}), attempting to re-login...")
                    if self.account and self.password and await self._login(self.account, self.password):
                        # Update authorization header in kwargs if it was passed explicitly
                        if "headers" in kwargs:
                            kwargs["headers"]["authorization"] = self.client.headers["authorization"]
                        logger.info("Re-login successful, retrying request...")
                        continue
                    else:
                        logger.error("Re-login failed, cannot retry request")
                        return resp
                
                return resp
                
            except httpx.TimeoutException as e:
                logger.warning(f"Request timeout (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                raise
            except httpx.ConnectError as e:
                logger.warning(f"Connection error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                logger.error(f"Request failed: {e}")
                raise
        
        # Should not reach here, but just in case
        raise RuntimeError("Request failed after all retries")
    
    # ==================== 业务方法（保持原有接口） ====================
    
    async def get_servings_today(self) -> Dict[str, Any]:
        """
        获取今日已出粮份数
        
        Path: /app/terminal/feeder/servingsToday
        Method: POST
        Payload: deviceId={device_id}
        """
        try:
            payload = {"deviceId": self.device_id}
            resp = await self._request("POST", "/app/terminal/feeder/servingsToday", data=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to get servings today: {e}")
            raise
    
    async def manual_feed(self, amount: int = 1) -> Dict[str, Any]:
        """
        立即喂食
        
        Path: /app/terminal/feeder/manualFeed
        Method: POST
        Payload: deviceId={device_id}&unit={amount}
        
        Args:
            amount: 喂食份数（默认1份）
            
        Returns:
            执行结果
        """
        try:
            if amount < 1:
                raise ValueError("Feeding amount must be at least 1")
            
            payload = {"deviceId": self.device_id, "unit": str(amount)}
            resp = await self._request("POST", "/app/terminal/feeder/manualFeed", data=payload)
            
            if resp.status_code != 200:
                logger.error(f"Manual feed failed with status {resp.status_code}: {resp.text}")
            
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Manual feed successful: {amount} servings")
            return {
                'success': True,
                'action': 'feed',
                'amount': amount,
                'device_id': self.device_id,
                'result': result
            }
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Manual feed failed: {e}")
            raise
    
    @staticmethod
    def _format_weekdays(weekdays: Any) -> str:
        """格式化星期数据为逗号分隔的字符串"""
        if isinstance(weekdays, list):
            if not weekdays:
                weekdays = DEFAULT_WEEKDAYS
            return ",".join(map(str, weekdays))
        elif isinstance(weekdays, str):
            return weekdays if weekdays else ",".join(map(str, DEFAULT_WEEKDAYS))
        else:
            return ",".join(map(str, DEFAULT_WEEKDAYS))
    
    @staticmethod
    def _parse_time(time_str: str) -> tuple:
        """解析时间字符串，返回 (hour, minute) 元组"""
        if not time_str or ':' not in time_str:
            raise ValueError(f"Invalid time format: {time_str}, expected HH:mm")
        
        parts = time_str.split(':')
        if len(parts) != 2:
            raise ValueError(f"Invalid time format: {time_str}, expected HH:mm")
        
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError:
            raise ValueError(f"Invalid time format: {time_str}, hour and minute must be integers")
        
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Invalid time values: hour={hour}, minute={minute}")
        
        return hour, minute
    
    async def get_feeding_plans(self) -> List[Dict[str, Any]]:
        """
        获取喂食计划列表
        
        Path: /app/terminal/feeder/planList/{device_id}
        Method: GET
        Params: deviceType={device_type}&pageNum=1&pageSize=1000
        """
        try:
            headers = self.client.headers.copy()
            headers.pop("Content-Type", None)
            
            url = f"/app/terminal/feeder/planList/{self.device_id}"
            params = {
                "deviceType": DEFAULT_DEVICE_TYPE,
                "pageNum": "1",
                "pageSize": "1000"
            }
            
            resp = await self._request("GET", url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            raw_list = []
            if "rows" in data:
                raw_list = data["rows"]
            elif "result" in data:
                if isinstance(data["result"], list):
                    raw_list = data["result"]
                elif isinstance(data["result"], dict) and "list" in data["result"]:
                    raw_list = data["result"]["list"]
            
            # Transform to FeedingPlan model
            plans = []
            for item in raw_list:
                try:
                    # Construct time HH:mm
                    hour = item.get("hour", 0)
                    minute = item.get("minute", 0)
                    time_str = f"{int(hour):02d}:{int(minute):02d}"
                    
                    plan = {
                        "id": str(item.get("id")),
                        "time": time_str,
                        "amount": item.get("serving", 1),
                        "enabled": item.get("enable", True),
                        "weekdays": item.get("daysOfWeek", []),
                        "remark": item.get("remark", "")
                    }
                    plans.append(plan)
                except Exception as e:
                    logger.error(f"Error parsing plan item: {e}")
                    continue
            
            return plans
        except Exception as e:
            logger.error(f"Failed to get feeding plans: {e}")
            return []
    
    async def add_feeding_plan(self, plan: FeedingPlan) -> Dict[str, Any]:
        """
        添加喂食计划
        
        Path: /app/terminal/feeder/addPlan
        Method: POST
        """
        try:
            hour, minute = self._parse_time(plan.time)
            weekdays_str = self._format_weekdays(plan.weekdays)
            
            payload = {
                "deviceId": self.device_id,
                "deviceType": DEFAULT_DEVICE_TYPE,
                "hour": str(hour),
                "minute": str(minute),
                "serving": str(plan.amount),
                "enable": "1" if plan.enabled else "0",
                "daysOfWeek": weekdays_str,
                "remark": plan.remark or ""
            }
            
            resp = await self._request("POST", "/app/terminal/feeder/addPlan", data=payload)
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Feeding plan added: {plan.time}, {plan.amount} servings")
            return result
        except Exception as e:
            logger.error(f"Failed to add feeding plan: {e}")
            raise
    
    async def update_feeding_plan(self, plan: FeedingPlan) -> Dict[str, Any]:
        """
        更新喂食计划
        
        Path: /app/terminal/feeder/updatePlan
        Method: POST
        """
        if not plan.id:
            raise ValueError("Plan ID is required for update")
        
        try:
            hour, minute = self._parse_time(plan.time)
            weekdays_str = self._format_weekdays(plan.weekdays)
            
            payload = {
                "id": plan.id,
                "deviceId": self.device_id,
                "deviceType": DEFAULT_DEVICE_TYPE,
                "hour": str(hour),
                "minute": str(minute),
                "serving": str(plan.amount),
                "enable": "1" if plan.enabled else "0",
                "daysOfWeek": weekdays_str,
                "remark": plan.remark or ""
            }
            
            resp = await self._request("POST", "/app/terminal/feeder/updatePlan", data=payload)
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Feeding plan updated: ID={plan.id}")
            return result
        except Exception as e:
            logger.error(f"Failed to update feeding plan: {e}")
            raise
    
    async def delete_feeding_plan(self, plan_id: str) -> Dict[str, Any]:
        """
        删除喂食计划
        
        Path: /app/terminal/feeder/deletePlan
        Method: POST
        """
        try:
            payload = {"id": plan_id}
            resp = await self._request("POST", "/app/terminal/feeder/deletePlan", data=payload)
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Feeding plan deleted: ID={plan_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to delete feeding plan: {e}")
            raise


# 导出别名，保持向后兼容
CloudPetsService = CloudPetsServiceRefactored
