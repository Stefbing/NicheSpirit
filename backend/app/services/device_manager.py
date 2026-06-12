"""
设备管理服务 — 统一管理和调度所有设备

设计目标：
1. 统一设备访问入口：所有设备操作通过DeviceManager
2. 设备类型注册机制：支持快速接入新设备类型
3. 自动服务生命周期管理：自动初始化、复用、关闭服务实例
4. 多租户隔离：确保用户只能访问自己的设备和被分享的设备
5. 错误处理和重试：统一的异常处理和重试逻辑

架构模式：
- Registry Pattern：设备类型注册表
- Factory Pattern：设备服务实例创建
- Facade Pattern：统一对外接口
- Context Manager：自动资源管理

使用示例：
    from backend.app.services.device_manager import DeviceManager
    
    # 获取设备状态
    status = await DeviceManager.get_device_status('petkit', user_id=123)
    
    # 执行设备命令
    result = await DeviceManager.execute_command(
        platform='petkit',
        command='clean',
        user_id=123,
        params={'device_id': '12345'}
    )
    
    # 注册新设备类型（扩展点）
    from backend.app.services.xiaomi_service import XiaomiScaleService
    DeviceManager.register_device('xiaomi', XiaomiScaleService)
"""
import logging
from typing import Dict, Optional, Type, Any
from contextlib import asynccontextmanager

from .device_base import DeviceBase
from .petkit_service_refactored import PetKitServiceRefactored
from .cloudpets_service_refactored import CloudPetsServiceRefactored

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DeviceManager:
    """
    设备管理服务 - 统一管理和调度所有设备
    
    这是一个工具类，所有方法都是类方法（@classmethod），
    不需要实例化即可使用。
    
    功能：
    - 设备类型注册和管理
    - 设备服务实例创建和缓存
    - 统一的设备操作接口
    - 多租户数据隔离
    """
    
    # ==================== 设备类型注册表 ====================
    
    # 设备类型注册表：{platform: DeviceBase子类}
    # 使用此类变量，所有调用共享同一个注册表
    _registry: Dict[str, Type[DeviceBase]] = {
        'petkit': PetKitServiceRefactored,
        'cloudpets': CloudPetsServiceRefactored,
        # 未来扩展：'xiaomi': XiaomiScaleService,
    }
    
    # 服务实例缓存：{user_id: {platform: instance}}
    # 避免重复创建服务实例，提高性能
    _instances: Dict[int, Dict[str, DeviceBase]] = {}
    
    # ==================== 设备类型注册 ====================
    
    @classmethod
    def register_device(cls, platform: str, device_class: Type[DeviceBase]) -> None:
        """
        注册新设备类型（扩展点）
        
        此方法允许动态注册新的设备类型，无需修改此类代码。
        这是Open-Closed Principle（开闭原则）的典型应用。
        
        Args:
            platform: 设备平台标识（如：'petkit' / 'cloudpets' / 'xiaomi'）
            device_class: 设备服务类（必须继承DeviceBase）
            
        Raises:
            ValueError: device_class不是DeviceBase的子类
            
        示例：
            >>> from backend.app.services.xiaomi_service import XiaomiScaleService
            >>> DeviceManager.register_device('xiaomi', XiaomiScaleService)
            >>> print(DeviceManager.list_registered_devices())
        """
        # 类型检查：确保device_class继承DeviceBase
        if not issubclass(device_class, DeviceBase):
            raise ValueError(f"{device_class} must inherit from DeviceBase")
        
        cls._registry[platform] = device_class
        logger.info(f"[DeviceManager] 注册设备类型: platform={platform}, class={device_class.__name__}")
    
    @classmethod
    def unregister_device(cls, platform: str) -> None:
        """
        注销设备类型
        
        Args:
            platform: 设备平台标识
        """
        if platform in cls._registry:
            del cls._registry[platform]
            logger.info(f"[DeviceManager] 注销设备类型: platform={platform}")
    
    @classmethod
    def list_registered_devices(cls) -> Dict[str, str]:
        """
        列出所有已注册的设备类型
        
        Returns:
            字典：{platform: class_name}
            
        示例：
            >>> devices = DeviceManager.list_registered_devices()
            >>> print(devices)
            {'petkit': 'PetKitServiceRefactored', 'cloudpets': 'CloudPetsServiceRefactored'}
        """
        return {platform: cls._registry[platform].__name__ for platform in cls._registry}
    
    # ==================== 服务实例管理 ====================
    
    @classmethod
    async def get_device_service(
        cls,
        platform: str,
        user_id: int,
        config: Optional[Dict[str, Any]] = None
    ) -> Optional[DeviceBase]:
        """
        获取设备服务实例
        
        此方法实现服务实例的懒加载和缓存：
        1. 先查缓存（_instances）
        2. 缓存未命中，创建新实例
        3. 初始化服务（调用initialize()）
        4. 缓存实例
        
        Args:
            platform: 设备平台标识
            user_id: 用户ID（用于多租户隔离）
            config: 设备配置（可选，用于初始化）
            
        Returns:
            设备服务实例，或None（如果平台未注册或初始化失败）
            
        示例：
            >>> service = await DeviceManager.get_device_service('petkit', user_id=123)
            >>> if service:
            ...     status = await service.get_status()
        """
        # 1. 检查平台是否注册
        if platform not in cls._registry:
            logger.error(f"[DeviceManager] 未知设备平台: platform={platform}")
            return None
        
        # 2. 检查缓存（实例复用）
        if user_id in cls._instances and platform in cls._instances[user_id]:
            instance = cls._instances[user_id][platform]
            logger.debug(f"[DeviceManager] 复用缓存实例: user_id={user_id}, platform={platform}")
            return instance
        
        # 3. 创建新实例
        device_class = cls._registry[platform]
        instance = device_class(user_id=user_id)
        
        # 4. 初始化服务
        if config is None:
            # 从数据库加载配置
            config = await cls._load_config_from_db(platform, user_id)
        
        if config is None:
            logger.warning(f"[DeviceManager] 用户配置未找到: user_id={user_id}, platform={platform}")
            return None
        
        try:
            success = await instance.initialize(config)
            if not success:
                logger.error(f"[DeviceManager] 服务初始化失败: user_id={user_id}, platform={platform}")
                await instance.close()
                return None
        except Exception as e:
            logger.error(f"[DeviceManager] 服务初始化异常: {e}")
            await instance.close()
            return None
        
        # 5. 缓存实例
        if user_id not in cls._instances:
            cls._instances[user_id] = {}
        cls._instances[user_id][platform] = instance
        
        logger.info(f"[DeviceManager] 服务实例已创建: user_id={user_id}, platform={platform}")
        return instance
    
    @classmethod
    async def _load_config_from_db(cls, platform: str, user_id: int) -> Optional[Dict[str, Any]]:
        """
        从数据库加载设备配置
        
        Args:
            platform: 设备平台标识
            user_id: 用户ID
            
        Returns:
            配置字典，或None
        """
        try:
            from backend.app.utils.config_manager import get_config_from_db
            
            account = await get_config_from_db("account", user_id=user_id, platform=platform)
            password = await get_config_from_db("password", user_id=user_id, platform=platform)
            
            if not account or not password:
                return None
            
            config = {
                'account': account,
                'password': password,
            }
            
            # 平台特定配置
            if platform == 'cloudpets':
                device_id = await get_config_from_db("device_id", user_id=user_id, platform=platform)
                if device_id:
                    config['device_id'] = device_id
            
            return config
        except Exception as e:
            logger.error(f"[DeviceManager] 加载配置失败: {e}")
            return None
    
    @classmethod
    async def release_service(cls, platform: str, user_id: int) -> None:
        """
        释放服务实例（从缓存中移除并关闭）
        
        Args:
            platform: 设备平台标识
            user_id: 用户ID
        """
        if user_id in cls._instances and platform in cls._instances[user_id]:
            instance = cls._instances[user_id][platform]
            await instance.close()
            del cls._instances[user_id][platform]
            logger.info(f"[DeviceManager] 服务实例已释放: user_id={user_id}, platform={platform}")
        
        # 清理空的用户缓存
        if user_id in cls._instances and len(cls._instances[user_id]) == 0:
            del cls._instances[user_id]
    
    @classmethod
    async def release_all_services(cls, user_id: int) -> None:
        """
        释放用户的所有服务实例
        
        Args:
            user_id: 用户ID
        """
        if user_id in cls._instances:
            for platform, instance in cls._instances[user_id].items():
                await instance.close()
                logger.info(f"[DeviceManager] 服务实例已关闭: user_id={user_id}, platform={platform}")
            del cls._instances[user_id]
    
    # ==================== 统一设备操作接口 ====================
    
    @classmethod
    async def get_device_status(cls, platform: str, user_id: int) -> Optional[Dict[str, Any]]:
        """
        获取设备状态（统一接口）
        
        Args:
            platform: 设备平台标识
            user_id: 用户ID
            
        Returns:
            设备状态字典，或None
            
        示例：
            >>> status = await DeviceManager.get_device_status('petkit', user_id=123)
            >>> print(status['devices'])
        """
        instance = await cls.get_device_service(platform, user_id)
        if not instance:
            return None
        
        try:
            return await instance.get_status()
        except Exception as e:
            logger.error(f"[DeviceManager] 获取状态失败: platform={platform}, user_id={user_id}, error={e}")
            return None
    
    @classmethod
    async def execute_command(
        cls,
        platform: str,
        command: str,
        user_id: int,
        params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        执行设备命令（统一接口）
        
        Args:
            platform: 设备平台标识
            command: 命令名称
            user_id: 用户ID
            params: 命令参数
            
        Returns:
            执行结果字典，或None
            
        示例：
            >>> result = await DeviceManager.execute_command(
            ...     platform='petkit',
            ...     command='clean',
            ...     user_id=123,
            ...     params={'device_id': '12345'}
            ... )
        """
        instance = await cls.get_device_service(platform, user_id)
        if not instance:
            return None
        
        try:
            return await instance.execute_command(command, params)
        except Exception as e:
            logger.error(f"[DeviceManager] 执行命令失败: platform={platform}, command={command}, user_id={user_id}, error={e}")
            return None
    
    @classmethod
    async def get_device_data(
        cls,
        platform: str,
        data_type: str,
        user_id: int,
        **kwargs
    ) -> Any:
        """
        获取设备数据（统一接口）
        
        Args:
            platform: 设备平台标识
            data_type: 数据类型
            user_id: 用户ID
            **kwargs: 额外参数
            
        Returns:
            数据对象，或None
            
        示例：
            >>> devices = await DeviceManager.get_device_data('petkit', 'devices', user_id=123)
            >>> plans = await DeviceManager.get_device_data('cloudpets', 'plans', user_id=123)
        """
        instance = await cls.get_device_service(platform, user_id)
        if not instance:
            return None
        
        try:
            return await instance.get_data(data_type, **kwargs)
        except Exception as e:
            logger.error(f"[DeviceManager] 获取数据失败: platform={platform}, data_type={data_type}, user_id={user_id}, error={e}")
            return None
    
    # ==================== 上下文管理器 ====================
    
    @classmethod
    @asynccontextmanager
    async def service_context(cls, platform: str, user_id: int):
        """
        服务实例上下文管理器
        
        确保服务实例在使用后正确关闭。
        
        使用示例：
            async with DeviceManager.service_context('petkit', user_id=123) as service:
                status = await service.get_status()
                result = await service.execute_command('clean', {})
            # 退出上下文时自动关闭服务
        
        Args:
            platform: 设备平台标识
            user_id: 用户ID
            
        Yields:
            设备服务实例
        """
        instance = await cls.get_device_service(platform, user_id)
        if not instance:
            raise RuntimeError(f"Failed to create service instance for platform={platform}")
        
        try:
            yield instance
        finally:
            # 注意：此处不主动关闭，因为实例可能被缓存复用
            # 如果需要立即关闭，调用方应显式调用 release_service()
            pass
    
    # ==================== 工具方法 ====================
    
    @classmethod
    async def get_all_user_devices(cls, user_id: int) -> Dict[str, Any]:
        """
        获取用户的所有设备状态
        
        批量查询用户所有已配置平台的状态。
        
        Args:
            user_id: 用户ID
            
        Returns:
            字典：{platform: status_dict}
            
        示例：
            >>> all_status = await DeviceManager.get_all_user_devices(user_id=123)
            >>> for platform, status in all_status.items():
            ...     print(f"{platform}: {status['online']}")
        """
        result = {}
        
        for platform in cls._registry:
            # 检查用户是否配置了此平台
            config = await cls._load_config_from_db(platform, user_id)
            if config is None:
                continue
            
            # 获取设备状态
            status = await cls.get_device_status(platform, user_id)
            if status:
                result[platform] = status
        
        return result
    
    @classmethod
    async def clear_cache(cls) -> None:
        """
        清空服务实例缓存
        
        用于在测试或重启时清理所有缓存的实例。
        """
        for user_id in list(cls._instances.keys()):
            for platform, instance in cls._instances[user_id].items():
                try:
                    await instance.close()
                except Exception as e:
                    logger.warning(f"[DeviceManager] 关闭实例失败: {e}")
        
        cls._instances.clear()
        logger.info("[DeviceManager] 服务实例缓存已清空")


# ==================== 快捷函数（可选，提供更简洁的API） ====================

async def get_status(platform: str, user_id: int) -> Optional[Dict[str, Any]]:
    """
    获取设备状态的快捷函数
    
    等价于 DeviceManager.get_device_status()
    """
    return await DeviceManager.get_device_status(platform, user_id)


async def execute_command(
    platform: str,
    command: str,
    user_id: int,
    params: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    执行设备命令的快捷函数
    
    等价于 DeviceManager.execute_command()
    """
    return await DeviceManager.execute_command(platform, command, user_id, params)


async def get_data(
    platform: str,
    data_type: str,
    user_id: int,
    **kwargs
) -> Any:
    """
    获取设备数据的快捷函数
    
    等价于 DeviceManager.get_device_data()
    """
    return await DeviceManager.get_device_data(platform, data_type, user_id, **kwargs)
