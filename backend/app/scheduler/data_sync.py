"""
后台数据同步任务 — 定时从 PetKit / CloudPets 第三方 API 拉取数据写入 Redis
实现 Dashboard 读写分离，主请求线程不再发起外网 HTTP 调用
"""
import asyncio
import logging
from sqlmodel import Session, select

from backend.app.models.db import engine
from backend.app.models.models import SystemConfig
from backend.app.services.petkit_service import PetKitService
from backend.app.services.cloudpets_service import CloudPetsService
from backend.app.utils.config_manager import get_config_from_db
from backend.app.utils.redis_cache import redis_cache

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
