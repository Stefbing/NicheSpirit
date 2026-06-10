"""
后台数据同步任务 — 定时从 PetKit / CloudPets 第三方 API 拉取数据写入 Redis
实现 Dashboard 读写分离，主请求线程不再发起外网 HTTP 调用
"""
import asyncio
import logging
from typing import Optional
from sqlmodel import Session, select

from ..models.db import engine
from ..models.models import SystemConfig
from ..services.petkit_service import PetKitService
from ..services.cloudpets_service import CloudPetsService
from ..utils.config_manager import get_config_from_db
from ..utils.redis_cache import redis_cache

logger = logging.getLogger(__name__)

CACHE_TTL = 600  # 10 分钟


# ── 工具函数 ──────────────────────────────────────────────

async def _first_user_id(platform: str) -> Optional[int]:
    """查找第一个配置了指定平台的用户 ID"""
    def _query():
        with Session(engine) as session:
            stmt = select(SystemConfig.user_id).where(
                SystemConfig.platform == platform,
                SystemConfig.key == "account",
            ).distinct()
            ids = session.exec(stmt).all()
            return ids[0] if ids else None
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _query)
    except Exception as e:
        logger.warning(f"[DataSync] 查询 {platform} 用户失败: {e}")
        return None


# ── PetKit 同步 ──────────────────────────────────────────

async def sync_petkit_data():
    """同步 PetKit 设备数据到 Redis 缓存"""
    user_id = await _first_user_id("petkit")
    if not user_id:
        logger.debug("[DataSync] 无 PetKit 用户，跳过")
        return

    account = await get_config_from_db("account", user_id=user_id, platform="petkit")
    password = await get_config_from_db("password", user_id=user_id, platform="petkit")
    if not account or not password:
        logger.warning("[DataSync] PetKit 凭据缺失")
        return

    service = PetKitService(account, password, user_id=user_id)
    try:
        ok = await service.initialize()
        if not ok:
            logger.error("[DataSync] PetKit 初始化失败")
            return

        devices = await service.get_devices()
        cache_key = f"user_{user_id}_petkit_devices"
        await redis_cache.set(cache_key, devices, ttl=CACHE_TTL)
        logger.info(f"[DataSync] ✓ PetKit 同步完成, {len(devices)} 台设备 → Redis")
    except Exception as e:
        logger.error(f"[DataSync] PetKit 同步异常: {e}")
    finally:
        await service.close()


# ── CloudPets 同步 ───────────────────────────────────────

async def sync_cloudpets_data():
    """同步 CloudPets 数据（今日出粮 + 喂食计划）到 Redis 缓存"""
    user_id = await _first_user_id("cloudpets")
    if not user_id:
        logger.debug("[DataSync] 无 CloudPets 用户，跳过")
        return

    account = await get_config_from_db("account", user_id=user_id, platform="cloudpets")
    password = await get_config_from_db("password", user_id=user_id, platform="cloudpets")
    if not account or not password:
        logger.warning("[DataSync] CloudPets 凭据缺失")
        return

    service = CloudPetsService(user_id=user_id)
    try:
        ok = await service.initialize(account=account, password=password)
        if not ok:
            logger.error("[DataSync] CloudPets 初始化失败")
            return

        # 今日出粮
        servings = await service.get_servings_today()
        if isinstance(servings, dict) and str(servings.get("code")) in ("401", "500", "403"):
            logger.warning(f"[DataSync] CloudPets servings API 错误: code={servings.get('code')}")
        else:
            await redis_cache.set(f"user_{user_id}_cloudpets_servings", servings, ttl=CACHE_TTL)

        # 喂食计划
        plans = await service.get_feeding_plans()
        if isinstance(plans, list):
            await redis_cache.set(f"user_{user_id}_cloudpets_plans", plans, ttl=CACHE_TTL)
            logger.info(f"[DataSync] ✓ CloudPets 同步完成, {len(plans)} 个计划 → Redis")
        elif isinstance(plans, dict) and str(plans.get("code")) in ("401", "500", "403"):
            logger.warning(f"[DataSync] CloudPets plans API 错误: code={plans.get('code')}")

    except Exception as e:
        logger.error(f"[DataSync] CloudPets 同步异常: {e}")
    finally:
        await service.close()
