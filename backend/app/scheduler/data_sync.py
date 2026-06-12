"""
后台数据同步任务 — 定时从 PetKit / CloudPets 第三方 API 拉取数据写入 Redis
实现 Dashboard 读写分离，主请求线程不再发起外网 HTTP 调用

【优化】集成Dashboard缓存管理器，实现智能缓存预热
"""
import asyncio
import logging
from sqlmodel import Session, select
from typing import List, Dict, Any

from backend.app.models.db import engine
from backend.app.models.models import SystemConfig, User
from backend.app.services.petkit_service import PetKitService
from backend.app.services.cloudpets_service import CloudPetsService
from backend.app.utils.config_manager import get_config_from_db
from backend.app.utils.redis_cache import redis_cache
from backend.app.utils.dashboard_cache_manager import get_dashboard_cache_manager

logger = logging.getLogger(__name__)

CACHE_TTL = 600  # 10 分钟


# ── 工具函数 ──────────────────────────────────────────────

async def _all_user_ids(platform: str) -> list[int]:
    """查找所有配置了指定平台的用户 ID 列表（而非仅第一个）
    【修复】支持设备分享场景：多个用户可能各自配置了不同平台的账号
    """
    def _query():
        with Session(engine) as session:
            stmt = select(SystemConfig.user_id).where(
                SystemConfig.platform == platform,
                SystemConfig.key == "account",
            ).distinct()
            return session.exec(stmt).all()
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _query) or []
    except Exception as e:
        logger.warning(f"[DataSync] 查询 {platform} 用户列表失败: {e}")
        return []


# ── PetKit 同步 ──────────────────────────────────────────

async def sync_petkit_data():
    """同步 PetKit 设备数据到 Redis 缓存（遍历所有配置用户）"""
    user_ids = await _all_user_ids("petkit")
    if not user_ids:
        logger.debug("[DataSync] 无 PetKit 用户，跳过")
        return

    success_count = 0
    for user_id in user_ids:
        try:
            account = await get_config_from_db("account", user_id=user_id, platform="petkit")
            password = await get_config_from_db("password", user_id=user_id, platform="petkit")
            if not account or not password:
                logger.warning(f"[DataSync] PetKit 用户 {user_id} 凭据缺失，跳过")
                continue

            service = PetKitService(account, password, user_id=user_id)
            try:
                ok = await service.initialize()
                if not ok:
                    logger.error(f"[DataSync] PetKit 用户 {user_id} 初始化失败")
                    continue

                devices = await service.get_devices()
                cache_key = f"user_{user_id}_petkit_devices"
                await redis_cache.set(cache_key, devices, ttl=CACHE_TTL)
                success_count += 1
                logger.debug(f"[DataSync] ✓ PetKit 用户 {user_id} 同步完成, {len(devices)} 台设备")
            finally:
                await service.close()
        except Exception as e:
            logger.error(f"[DataSync] PetKit 用户 {user_id} 同步异常: {e}")

    logger.info(f"[DataSync] ✓ PetKit 全量同步完成, {success_count}/{len(user_ids)} 个用户 → Redis")

    # 【优化】同步完成后，预热Dashboard缓存
    if success_count > 0:
        await _warmup_dashboard_cache(user_ids[:success_count], "petkit")


# ── CloudPets 同步 ───────────────────────────────────────

async def sync_cloudpets_data():
    """同步 CloudPets 数据（今日出粮 + 喂食计划）到 Redis 缓存（遍历所有配置用户）"""
    user_ids = await _all_user_ids("cloudpets")
    if not user_ids:
        logger.debug("[DataSync] 无 CloudPets 用户，跳过")
        return

    success_count = 0
    for user_id in user_ids:
        try:
            account = await get_config_from_db("account", user_id=user_id, platform="cloudpets")
            password = await get_config_from_db("password", user_id=user_id, platform="cloudpets")
            if not account or not password:
                logger.warning(f"[DataSync] CloudPets 用户 {user_id} 凭据缺失，跳过")
                continue

            service = CloudPetsService(user_id=user_id)
            try:
                ok = await service.initialize(account=account, password=password)
                if not ok:
                    logger.error(f"[DataSync] CloudPets 用户 {user_id} 初始化失败")
                    continue

                # 今日出粮
                servings = await service.get_servings_today()
                if isinstance(servings, dict) and str(servings.get("code")) in ("401", "500", "403"):
                    logger.warning(f"[DataSync] CloudPets 用户 {user_id} servings API 错误: code={servings.get('code')}")
                else:
                    await redis_cache.set(f"user_{user_id}_cloudpets_servings", servings, ttl=CACHE_TTL)

                # 喂食计划
                plans = await service.get_feeding_plans()
                if isinstance(plans, list):
                    await redis_cache.set(f"user_{user_id}_cloudpets_plans", plans, ttl=CACHE_TTL)
                    logger.debug(f"[DataSync] ✓ CloudPets 用户 {user_id} 同步完成, {len(plans)} 个计划")
                elif isinstance(plans, dict) and str(plans.get("code")) in ("401", "500", "403"):
                    logger.warning(f"[DataSync] CloudPets 用户 {user_id} plans API 错误: code={plans.get('code')}")

                success_count += 1
            finally:
                await service.close()
        except Exception as e:
            logger.error(f"[DataSync] CloudPets 用户 {user_id} 同步异常: {e}")

    logger.info(f"[DataSync] ✓ CloudPets 全量同步完成, {success_count}/{len(user_ids)} 个用户 → Redis")

    # 【优化】同步完成后，预热Dashboard缓存
    if success_count > 0:
        await _warmup_dashboard_cache(user_ids[:success_count], "cloudpets")


# ── 缓存预热辅助函数 ───────────────────────────────────────

async def _warmup_dashboard_cache(user_ids: List[int], platform: str):
    """
    预热指定用户的Dashboard缓存
    
    在数据同步完成后调用，主动更新Dashboard整体缓存和组件缓存
    
    Args:
        user_ids: 用户ID列表
        platform: 平台名称（petkit/cloudpets）
    """
    try:
        from backend.app.utils.dashboard_cache_manager import get_dashboard_cache_manager
        from backend.app.utils.redis_cache import redis_cache
        
        cache_manager = get_dashboard_cache_manager(redis_cache)
        
        logger.info(f"[DataSync] 开始预热Dashboard缓存 platform={platform} users={len(user_ids)}")
        
        # 批量预热（控制并发数）
        batch_size = 5
        for i in range(0, len(user_ids), batch_size):
            batch = user_ids[i:i + batch_size]
            
            # 并发预热当前批次
            tasks = [_warmup_single_user_dashboard(uid, cache_manager) for uid in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 统计成功/失败
            success = sum(1 for r in results if r is True)
            logger.debug(f"[DataSync] 预热批次 {i//batch_size + 1} 完成: {success}/{len(batch)}")
        
        logger.info(f"[DataSync] ✓ Dashboard缓存预热完成 platform={platform}")
    except Exception as e:
        logger.error(f"[DataSync] Dashboard缓存预热失败: {e}")


async def _warmup_single_user_dashboard(user_id: int, cache_manager) -> bool:
    """
    预热单个用户的Dashboard缓存（仅L2组件缓存）
    
    【修复】不再预热L1整体缓存，因为：
    1. L1缓存需要完整的Dashboard数据（包括device_platforms、shared_creds等）
    2. 数据同步任务只能提供部分组件数据（petkit_devices、cloudpets_servings等）
    3. 避免L1缓存中存储不完整数据，导致前端渲染失败
    
    L1缓存会在用户实际请求Dashboard数据时设置（通过API端点）
    
    Args:
        user_id: 用户ID
        cache_manager: Dashboard缓存管理器实例
        
    Returns:
        是否成功
    """
    try:
        warmup_count = 0
        
        # 1. 预热L2组件缓存（从Redis读取最新数据）
        # PetKit设备
        petkit_devices = await redis_cache.get(f"user_{user_id}_petkit_devices")
        if petkit_devices is not None:
            await cache_manager.set_component_cache(user_id, 'petkit_devices', petkit_devices)
            warmup_count += 1
        
        # CloudPets投喂记录
        cloudpets_servings = await redis_cache.get(f"user_{user_id}_cloudpets_servings")
        if cloudpets_servings is not None:
            await cache_manager.set_component_cache(user_id, 'cloudpets_servings', cloudpets_servings)
            warmup_count += 1
        
        # CloudPets喂食计划
        cloudpets_plans = await redis_cache.get(f"user_{user_id}_cloudpets_plans")
        if cloudpets_plans is not None:
            await cache_manager.set_component_cache(user_id, 'cloudpets_plans', cloudpets_plans)
            warmup_count += 1
        
        # 体脂秤统计
        scale_stats = await redis_cache.get(f"user_{user_id}_scale_stats")
        if scale_stats is not None:
            await cache_manager.set_component_cache(user_id, 'scale_stats', scale_stats)
            warmup_count += 1
        
        # 2. 【修复】不再预热L1整体缓存，避免存储不完整数据
        # L1缓存会在用户实际请求Dashboard数据时设置
        
        logger.debug(f"[DataSync] 预热L2缓存完成 user_id={user_id} components={warmup_count}")
        return True
    except Exception as e:
        logger.warning(f"[DataSync] 预热用户Dashboard缓存失败 user_id={user_id}: {e}")
        return False

