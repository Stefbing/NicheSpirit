"""
配置管理工具 - 从数据库读取加密配置，支持设备管理（性能优化版）
"""
import logging
import time
import asyncio
from typing import Optional, List, Dict, Any, Callable, TypeVar, Tuple
from sqlmodel import Session, select
from sqlalchemy import or_
from ..models.db import engine
from ..models.models import SystemConfig
from .config_encryptor import ConfigEncryptor

logger = logging.getLogger(__name__)

T = TypeVar('T')

# 全局线程池执行器（复用，避免频繁创建）
_executor = None


def _get_executor():
    """获取或创建线程池执行器"""
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db-worker")
    return _executor


def _get_timestamp_ms() -> int:
    """获取当前时间戳（毫秒）"""
    return int(time.time() * 1000)


async def _run_db_operation(func: Callable[[], T]) -> T:
    """
    在线程池中运行同步数据库操作，避免阻塞事件循环
    :param func: 同步数据库操作函数
    :return: 操作结果
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func)


async def get_config_from_db(key: str, user_id: Optional[int] = None, platform: Optional[str] = None, default: Optional[str] = None) -> Optional[str]:
    """
    从数据库获取配置值（自动解密）
    :param key: 配置键（如：account, password, app_version）
    :param user_id: 用户ID（可选，不提供则查询全局配置）
    :param platform: 平台过滤（可选，用于设备配置）
    :param default: 默认值
    :return: 配置值或默认值
    """
    def _get() -> Optional[str]:
        with Session(engine) as session:
            statement = select(SystemConfig).where(
                SystemConfig.key == key,
                SystemConfig.is_active == True
            )
            
            if user_id is not None:
                statement = statement.where(SystemConfig.user_id == user_id)
            else:
                statement = statement.where(SystemConfig.user_id == 0)
            
            if platform:
                statement = statement.where(SystemConfig.platform == platform)
            
            config = session.exec(statement.order_by(SystemConfig.id.desc())).first()
            
            if config:
                if config.is_encrypted:
                    return ConfigEncryptor.decrypt(config.value)
                return config.value
            return default

    try:
        return await _run_db_operation(_get)
    except Exception as e:
        logger.warning(f"Failed to get config {key} from database: {e}")
        return default


async def get_configs_batch(keys_users: List[Tuple[str, Optional[int], Optional[str]]]) -> Dict[str, Optional[str]]:
    """
    批量获取配置（减少数据库查询次数）
    :param keys_users: [(key, user_id, platform), ...]
    :return: {f"{key}_{user_id}_{platform}": value}
    """
    def _batch_get() -> Dict[str, Optional[str]]:
        results = {}
        with Session(engine) as session:
            for key, user_id, platform in keys_users:
                statement = select(SystemConfig).where(
                    SystemConfig.key == key,
                    SystemConfig.is_active == True
                )
                
                if user_id is not None:
                    statement = statement.where(SystemConfig.user_id == user_id)
                else:
                    statement = statement.where(SystemConfig.user_id == 0)
                
                if platform:
                    statement = statement.where(SystemConfig.platform == platform)
                
                # 【修复】排除共享配置（device_name以"shared_"开头），防止旧共享假凭据污染仪表盘
                statement = statement.where(
                    or_(
                        SystemConfig.device_name.is_(None),
                        ~SystemConfig.device_name.like('shared_%')
                    )
                )
                
                config = session.exec(statement.order_by(SystemConfig.id.desc())).first()
                
                cache_key = f"{key}_{user_id or 0}_{platform or 'global'}"
                if config:
                    results[cache_key] = ConfigEncryptor.decrypt(config.value) if config.is_encrypted else config.value
                else:
                    results[cache_key] = None
        return results

    try:
        return await _run_db_operation(_batch_get)
    except Exception as e:
        logger.error(f"Failed to batch get configs: {e}")
        return {}


async def set_config_to_db(key: str, user_id: int, value: str, is_encrypted: bool = False, 
                     platform: Optional[str] = None, device_name: Optional[str] = None):
    """
    保存配置到数据库（自动加密）
    :param key: 配置键（如：account, password）
    :param user_id: 用户ID
    :param value: 配置值
    :param is_encrypted: 是否加密存储
    :param platform: 平台名称（设备配置专用）
    :param device_name: 设备名称（设备配置专用）
    """
    def _set():
        with Session(engine) as session:
            # 查找现有配置
            statement = select(SystemConfig).where(
                SystemConfig.user_id == user_id,
                SystemConfig.key == key
            )
            
            if platform:
                statement = statement.where(SystemConfig.platform == platform)
            if device_name:
                statement = statement.where(SystemConfig.device_name == device_name)
            
            config = session.exec(statement).first()
            
            processed_value = ConfigEncryptor.encrypt(value) if is_encrypted else value
            timestamp = _get_timestamp_ms()
            
            if config:
                # 更新现有配置
                config.value = processed_value
                config.is_encrypted = is_encrypted
                config.updated_at = timestamp
            else:
                # 创建新配置
                config = SystemConfig(
                    user_id=user_id,
                    key=key,
                    value=processed_value,
                    is_encrypted=is_encrypted,
                    platform=platform,
                    device_name=device_name,
                    updated_at=timestamp
                )
                session.add(config)
            
            session.commit()
            logger.info(f"Config {key} saved for user {user_id}")

    try:
        await _run_db_operation(_set)
    except Exception as e:
        logger.error(f"Failed to save config {key} for user {user_id}: {e}")
        raise


async def get_user_devices(user_id: int, platform: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取用户的设备列表（从systemconfig表中查询）
    :param user_id: 用户ID
    :param platform: 平台过滤（可选）
    :return: 设备列表，每个设备包含 credentials（account/password）
    """
    def _get_devices() -> List[Dict[str, Any]]:
        with Session(engine) as session:
            statement = select(SystemConfig).where(
                SystemConfig.user_id == user_id,
                SystemConfig.platform.isnot(None),
                SystemConfig.is_active == True,
            ).where(
                or_(
                    SystemConfig.device_name.is_(None),
                    ~SystemConfig.device_name.like('shared_%')
                )
            )
            
            if platform:
                statement = statement.where(SystemConfig.platform == platform)
            
            configs = session.exec(statement).all()
            
            # 按 (platform, device_name) 分组
            devices_dict = {}
            for config in configs:
                device_key = (config.platform, config.device_name or "unknown")
                
                if device_key not in devices_dict:
                    devices_dict[device_key] = {
                        'platform': config.platform,
                        'device_name': config.device_name,
                        'device_key': f"{config.platform}_{config.device_name}" if config.device_name else config.platform,
                        'is_ble': config.key == 'ble_address',
                        'credentials': {}
                    }
                
                field_name = config.key
                if field_name in ('account', 'password', 'token'):
                    if config.is_encrypted:
                        devices_dict[device_key]['credentials'][field_name] = ConfigEncryptor.decrypt(config.value)
                    else:
                        devices_dict[device_key]['credentials'][field_name] = config.value
                else:
                    devices_dict[device_key][field_name] = config.value if config.value else ''
            
            # 标记完整性
            for d in devices_dict.values():
                if d.get('is_ble'):
                    d['is_complete'] = bool(d.get('ble_address'))
                else:
                    creds = d.get('credentials', {})
                    d['is_complete'] = bool(creds.get('account')) and bool(creds.get('password'))
                    d['has_token'] = bool(creds.get('token'))
            
            return list(devices_dict.values())

    try:
        return await _run_db_operation(_get_devices)
    except Exception as e:
        logger.error(f"Failed to get devices for user {user_id}: {e}")
        return []


async def add_device(user_id: int, platform: str, account: str, password: str, 
               device_name: Optional[str] = None) -> str:
    """
    添加设备到用户账户（保留旧接口，仅存 account+password）
    :param user_id: 用户ID
    :param platform: 平台名称
    :param account: 账号
    :param password: 密码
    :param device_name: 设备名称（可选，不传则用platform作为名称）
    :return: 设备标识符（device_name）
    """
    return await add_cloud_device(user_id, platform, account, password, device_name=device_name)


async def add_cloud_device(user_id: int, platform: str, account: str, password: str,
                          token: str = '', device_name: Optional[str] = None) -> str:
    """
    添加云设备 - 存储 account + password + token，device_name 为真实设备名
    :param user_id: 用户ID
    :param platform: 平台名称（如 petkit, cloudpets）
    :param account: 账号
    :param password: 密码
    :param token: 登录令牌（初始化后获取）
    :param device_name: 真实设备名称（如"小佩智能全自动猫厕所 MAX2"），不传则用 platform
    :return: 设备标识符
    """
    def _add() -> str:
        final_device_name = device_name or platform
        timestamp = _get_timestamp_ms()
        
        with Session(engine) as session:
            try:
                encrypted_account = ConfigEncryptor.encrypt(account)
                encrypted_password = ConfigEncryptor.encrypt(password)
                
                def upsert_config(key: str, val: str, encrypted: bool = True):
                    stmt = select(SystemConfig).where(
                        SystemConfig.user_id == user_id,
                        SystemConfig.key == key,
                        SystemConfig.platform == platform,
                        SystemConfig.device_name == final_device_name
                    )
                    cfg = session.exec(stmt).first()
                    
                    if cfg:
                        cfg.value = ConfigEncryptor.encrypt(val) if encrypted else val
                        cfg.is_encrypted = encrypted
                        cfg.is_active = True
                        cfg.updated_at = timestamp
                    else:
                        cfg = SystemConfig(
                            user_id=user_id,
                            key=key,
                            value=ConfigEncryptor.encrypt(val) if encrypted else val,
                            is_encrypted=encrypted,
                            platform=platform,
                            device_name=final_device_name,
                            is_active=True,
                            updated_at=timestamp
                        )
                        session.add(cfg)

                upsert_config("account", account)
                upsert_config("password", password)
                if token:
                    upsert_config("token", token)
                
                session.commit()
                logger.info(f"[Device] 云设备已保存: platform={platform}, name={final_device_name}, user={user_id}")
                return final_device_name
            except Exception:
                session.rollback()
                raise

    try:
        return await _run_db_operation(_add)
    except Exception as e:
        logger.error(f"[Device] 保存云设备失败: {e}")
        raise


async def add_ble_device(user_id: int, ble_address: str, device_name: str) -> str:
    """
    添加本地蓝牙设备 - 仅存一条 ble_address 记录
    :param user_id: 用户ID
    :param ble_address: BLE MAC 地址
    :param device_name: BLE 设备名（如 "MIBFS"）
    :return: 设备标识符
    """
    def _add() -> str:
        timestamp = _get_timestamp_ms()
        platform = 'xiaomi'
        
        with Session(engine) as session:
            try:
                stmt = select(SystemConfig).where(
                    SystemConfig.user_id == user_id,
                    SystemConfig.key == 'ble_address',
                    SystemConfig.platform == platform,
                )
                cfg = session.exec(stmt).first()
                
                if cfg:
                    cfg.value = ble_address
                    cfg.is_encrypted = False
                    cfg.is_active = True
                    cfg.device_name = device_name
                    cfg.updated_at = timestamp
                else:
                    cfg = SystemConfig(
                        user_id=user_id,
                        key='ble_address',
                        value=ble_address,
                        is_encrypted=False,
                        platform=platform,
                        device_name=device_name,
                        is_active=True,
                        updated_at=timestamp
                    )
                    session.add(cfg)
                
                session.commit()
                logger.info(f"[Device] BLE设备已保存: platform={platform}, name={device_name}, user={user_id}")
                return f"{platform}_{device_name}"
            except Exception:
                session.rollback()
                raise

    try:
        return await _run_db_operation(_add)
    except Exception as e:
        logger.error(f"[Device] 保存BLE设备失败: {e}")
        raise


async def delete_device_by_platform(user_id: int, platform: str) -> bool:
    """
    按平台删除用户的所有设备配置（软删除）
    :param user_id: 用户ID
    :param platform: 平台名称
    :return: 是否删除成功
    """
    def _delete() -> bool:
        with Session(engine) as session:
            stmt = select(SystemConfig).where(
                SystemConfig.user_id == user_id,
                SystemConfig.platform == platform,
                SystemConfig.is_active == True
            )
            configs = session.exec(stmt).all()
            
            if not configs:
                logger.warning(f"[Device] 平台 {platform} 无活跃配置，user={user_id}")
                return False
            
            timestamp = _get_timestamp_ms()
            for config in configs:
                config.is_active = False
                config.updated_at = timestamp
            
            session.commit()
            logger.info(f"[Device] 平台 {platform} 已软删除 ({len(configs)} 条), user={user_id}")
            return True

    try:
        return await _run_db_operation(_delete)
    except Exception as e:
        logger.error(f"[Device] 删除平台 {platform} 失败: {e}")
        raise


async def update_cloud_device_token(user_id: int, platform: str, token: str,
                                    device_name: Optional[str] = None) -> bool:
    """
    更新云设备的 token 记录（服务层刷新 token 后调用，保持设备配置组完整）
    :param user_id: 用户ID
    :param platform: 平台名称
    :param token: 新 token 值
    :param device_name: 设备名称（可选，不传则用 platform）
    :return: 是否更新成功
    """
    def _update() -> bool:
        final_device_name = device_name or platform
        timestamp = _get_timestamp_ms()

        with Session(engine) as session:
            stmt = select(SystemConfig).where(
                SystemConfig.user_id == user_id,
                SystemConfig.key == 'token',
                SystemConfig.platform == platform,
                SystemConfig.device_name == final_device_name,
                SystemConfig.is_active == True,
            )
            cfg = session.exec(stmt).first()

            if cfg:
                cfg.value = ConfigEncryptor.encrypt(token)
                cfg.is_encrypted = True
                cfg.updated_at = timestamp
                session.add(cfg)
            else:
                cfg = SystemConfig(
                    user_id=user_id,
                    key='token',
                    value=ConfigEncryptor.encrypt(token),
                    is_encrypted=True,
                    platform=platform,
                    device_name=final_device_name,
                    is_active=True,
                    updated_at=timestamp,
                )
                session.add(cfg)

            session.commit()
            logger.info(f"[Device] token 已更新: platform={platform}, name={final_device_name}, user={user_id}")
            return True

    try:
        return await _run_db_operation(_update)
    except Exception as e:
        logger.error(f"[Device] token 更新失败: {e}")
        return False


async def get_shared_devices_for_user(user_id: int) -> List[Dict[str, Any]]:
    """
    获取分享给该用户的设备配置列表（从 shared_device_config 表读取）
    返回：每个共享设备包含 platform, device_key, 以及分享者的原始凭据
    """
    def _get_shared() -> List[Dict[str, Any]]:
        with Session(engine) as session:
            from ..models.models import SharedDeviceConfig, DeviceShare, SystemConfig

            # 查询该用户收到的所有有效分享
            shares = session.exec(
                select(DeviceShare).where(
                    DeviceShare.to_user_id == user_id,
                    DeviceShare.status == "accepted"
                )
            ).all()

            result = []
            for share in shares:
                # 获取分享者 A 的原始凭据
                device_keys = json.loads(share.device_keys) if share.device_keys else []
                platforms = set()
                for dk in device_keys:
                    parts = dk.split('_', 1)
                    if parts:
                        platforms.add(parts[0])

                if not platforms:
                    continue

                # 读取 A 的原始凭据
                configs = session.exec(
                    select(SystemConfig).where(
                        SystemConfig.user_id == share.from_user_id,
                        SystemConfig.key.in_(["account", "password"]),
                        SystemConfig.platform.in_(list(platforms)),
                        SystemConfig.is_active == True
                    )
                ).all()

                from_creds = {}
                for cfg in configs:
                    plat = cfg.platform
                    if plat not in from_creds:
                        from_creds[plat] = {}
                    if cfg.is_encrypted:
                        from ..utils.config_encryptor import ConfigEncryptor
                        from_creds[plat][cfg.key] = ConfigEncryptor.decrypt(cfg.value)
                    else:
                        from_creds[plat][cfg.key] = cfg.value

                for dk in device_keys:
                    dk_platform = dk.split('_', 1)[0]
                    creds = from_creds.get(dk_platform, {})
                    result.append({
                        'platform': dk_platform,
                        'device_key': dk,
                        'share_id': share.id,
                        'from_user_id': share.from_user_id,
                        'account': creds.get('account', ''),
                        'password': creds.get('password', ''),
                    })

            return result

    try:
        return await _run_db_operation(_get_shared)
    except Exception as e:
        logger.error(f"Failed to get shared devices for user {user_id}: {e}")
        return []


async def delete_device(user_id: int, device_key: str) -> bool:
    """
    删除用户的设备（软删除，设置is_active=False）
    :param user_id: 用户ID
    :param device_key: 设备标识符（格式：platform_device_name）
    :return: 是否删除成功
    """
    def _delete_device() -> bool:
        # 解析 device_key: "cloudpets_cloudpets"
        parts = device_key.split('_', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid device_key format: {device_key}")
        
        platform, device_name = parts
        
        with Session(engine) as session:
            try:
                # 查询该设备的所有配置（account、password等）
                stmt = select(SystemConfig).where(
                    SystemConfig.user_id == user_id,
                    SystemConfig.platform == platform,
                    SystemConfig.device_name == device_name,
                    SystemConfig.is_active == True  # 只查询未删除的配置
                )
                configs = session.exec(stmt).all()
                
                if not configs:
                    logger.warning(f"Device {device_key} not found for user {user_id}")
                    return False
                
                # 软删除：设置is_active=False
                timestamp = _get_timestamp_ms()
                for config in configs:
                    config.is_active = False
                    config.updated_at = timestamp
                
                session.commit()
                logger.info(f"Device {device_key} soft-deleted for user {user_id} ({len(configs)} configs marked as inactive)")
                return True
            except Exception:
                session.rollback()
                raise

    try:
        return await _run_db_operation(_delete_device)
    except Exception as e:
        logger.error(f"Failed to delete device {device_key} for user {user_id}: {e}")
        raise

