"""
设备抽象层 — 所有设备类型必须实现此接口

设计原则：
1. 统一接口：所有设备类型（PetKit/CloudPets/Xiaomi）实现相同接口
2. 异步优先：所有方法都是async，支持高并发
3. 类型安全：使用Python Type Hints，便于IDE提示和静态检查
4. 资源安全：实现context manager协议，确保资源正确释放
5. 可扩展性：新增设备类型只需继承此类并实现接口

使用示例：
    class PetKitService(DeviceBase):
        async def initialize(self, config: Dict[str, Any]) -> bool:
            # 实现初始化逻辑
            pass
        
        # ... 实现其他抽象方法
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager


class DeviceBase(ABC):
    """
    设备抽象基类 - 定义所有设备类型的统一接口
    
    这是一个抽象基类(ABC)，所有设备服务必须继承此类并实现所有抽象方法。
    
    设计模式：
    - Template Method：定义算法骨架，子类实现具体步骤
    - Strategy：不同设备使用不同策略，但接口统一
    - Context Manager：支持异步上下文管理，自动释放资源
    """
    
    # ==================== 抽象属性 ====================
    
    @property
    @abstractmethod
    def platform(self) -> str:
        """
        设备平台标识
        
        Returns:
            平台标识字符串，如：'petkit' / 'cloudpets' / 'xiaomi'
        
        示例：
            >>> @property
            ... def platform(self) -> str:
            ...     return 'petkit'
        """
        pass
    
    @property
    @abstractmethod
    def device_type(self) -> str:
        """
        设备类型
        
        Returns:
            设备类型字符串，如：'litterbox' / 'feeder' / 'scale'
        
        示例：
            >>> @property
            ... def device_type(self) -> str:
            ...     return 'litterbox'
        """
        pass
    
    # ==================== 抽象方法 ====================
    
    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """
        初始化设备连接
        
        此方法负责：
        1. 验证配置参数（account/password/device_id等）
        2. 建立与设备/第三方API的连接
        3. 加载会话缓存（如果有）
        4. 测试连接可用性
        
        Args:
            config: 设备配置字典，可能包含：
                - account: 账号（第三方平台）
                - password: 密码
                - device_id: 设备ID
                - extra: 其他扩展参数
                
        Returns:
            bool: 初始化是否成功
            
        Raises:
            ValueError: 配置参数无效
            ConnectionError: 无法连接设备/API
            
        示例：
            >>> service = PetKitService()
            >>> success = await service.initialize({
            ...     'account': 'user@example.com',
            ...     'password': 'secret'
            ... })
            >>> if success:
            ...     print("初始化成功")
        """
        pass
    
    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """
        获取设备状态
        
        返回设备的实时状态信息，如：
        - 在线状态
        - 电量/电量百分比
        - 工作状态（空闲/运行中/错误）
        - 设备特定状态（如猫厕所的清洁状态）
        
        Returns:
            状态字典，结构因设备类型而异，但应包含：
            {
                'online': bool,          # 是否在线
                'devices': [             # 设备列表（支持多设备）
                    {
                        'device_id': str,
                        'device_name': str,
                        'work_state': str,
                        'battery': int,   # 电量百分比（可选）
                        # ... 设备特定字段
                    }
                ]
            }
            
        示例：
            >>> status = await service.get_status()
            >>> print(status['devices'][0]['work_state'])
        """
        pass
    
    @abstractmethod
    async def execute_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行设备控制命令
        
        统一的命令执行接口，支持所有设备类型的控制操作。
        
        Args:
            command: 命令名称，预定义的命令：
                - 'clean': 清洗（猫厕所）
                - 'deodorize': 除臭（猫厕所）
                - 'feed': 喂食（喂食机）
                - 'start_measure': 开始测量（体脂秤）
                - 设备特定命令：由具体实现定义
                
            params: 命令参数，因命令而异：
                - device_id: 目标设备ID（可选，默认第一个设备）
                - 其他命令特定参数
                
        Returns:
            执行结果字典：
            {
                'success': bool,         # 是否成功
                'message': str,          # 结果消息
                'data': Any,            # 返回数据（可选）
                'timestamp': int,        # 执行时间戳（毫秒）
            }
            
        Raises:
            ValueError: 未知命令或参数无效
            RuntimeError: 命令执行失败
            
        示例：
            >>> result = await service.execute_command('clean', {
            ...     'device_id': '12345'
            ... })
            >>> if result['success']:
            ...     print("清洗命令已发送")
        """
        pass
    
    @abstractmethod
    async def get_data(self, data_type: str, **kwargs) -> Any:
        """
        获取设备数据
        
        获取设备的历史数据或配置数据。
        
        Args:
            data_type: 数据类型，预定义的类型：
                - 'devices': 设备列表
                - 'servings': 喂食记录（喂食机）
                - 'plans': 喂食计划（喂食机）
                - 'weight': 体重记录（体脂秤）
                - 设备特定类型：由具体实现定义
                
            **kwargs: 额外参数，因数据类型而异：
                - limit: 限制返回数量
                - start_time: 开始时间
                - end_time: 结束时间
                
        Returns:
            数据对象，类型取决于data_type：
            - list: 多个数据记录
            - dict: 单个数据对象
            - None: 无数据
            
        示例：
            >>> devices = await service.get_data('devices')
            >>> for device in devices:
            ...     print(device['device_name'])
        """
        pass
    
    @abstractmethod
    async def close(self):
        """
        关闭设备连接，释放资源
        
        此方法负责：
        1. 关闭网络连接
        2. 保存会话状态（如果需要）
        3. 释放锁、文件句柄等资源
        4. 标记实例为未初始化状态
        
        注意：
        - 此方法应该幂等（多次调用不会产生副作用）
        - 此方法不应该抛出异常（记录日志即可）
        - 建议在finally块中调用此方法
        
        示例：
            >>> try:
            ...     await service.initialize(config)
            ...     status = await service.get_status()
            ... finally:
            ...     await service.close()
        """
        pass
    
    # ==================== 上下文管理器支持 ====================
    
    async def __aenter__(self):
        """
        异步上下文管理器入口
        
        支持用法：
            async with PetKitService() as service:
                await service.initialize(config)
                status = await service.get_status()
        
        Returns:
            DeviceBase: 自身实例
        """
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        异步上下文管理器退出
        
        确保无论是否发生异常，都会调用close()释放资源
        """
        await self.close()
    
    # ==================== 通用工具方法 ====================
    
    def _validate_config(self, config: Dict[str, Any], required_keys: List[str]) -> None:
        """
        验证配置参数
        
        Args:
            config: 配置字典
            required_keys: 必需的键列表
            
        Raises:
            ValueError: 缺少必需参数
        """
        missing = [key for key in required_keys if key not in config or not config[key]]
        if missing:
            raise ValueError(f"缺少必需配置: {', '.join(missing)}")
    
    def _format_device_status(self, device_id: str, device_name: str, **kwargs) -> Dict[str, Any]:
        """
        格式化设备状态（辅助方法，子类可复用）
        
        Args:
            device_id: 设备ID
            device_name: 设备名称
            **kwargs: 其他状态字段
            
        Returns:
            格式化的状态字典
        """
        return {
            'device_id': device_id,
            'device_name': device_name,
            **kwargs
        }
    
    def __repr__(self) -> str:
        """
        字符串表示
        
        Returns:
            包含平台和设备类型的字符串
        """
        return f"{self.__class__.__name__}(platform='{self.platform}', device_type='{self.device_type}')"
