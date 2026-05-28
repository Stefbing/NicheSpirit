"""Smart Home Controller API - Main Application"""
import os, uvicorn, asyncio, time, logging, hashlib, hmac, json, secrets
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional, List

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
# StaticFiles / FileResponse 已移除（静态页面废弃）
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlmodel import Session, select

from .services.petkit_service import PetKitService
from .services.cloudpets_service import cloudpets_service, FeedingPlan as CloudPetsPlan

from .models.models import User, WeightRecord, SystemConfig, FamilyMember
from .models.db import get_session, init_db, engine
from .utils.cache_manager import cache_manager
from .utils.config_encryptor import ConfigEncryptor
from .scheduler.task_scheduler import scheduler
from .share_routes import router as share_router

load_dotenv()

# --- AppState & Helpers ---
class AppState:
    def __init__(self):
        self.petkit: Optional[PetKitService] = None
        self.petkit_lock = asyncio.Lock()      # PetKit 全局实例互斥锁
        self.cloudpets = None
        self.cloudpets_lock = asyncio.Lock()    # CloudPets 全局实例互斥锁
        self.data_refresh_task = None

state = AppState()

# 后台任务集合（防止 GC 回收导致任务丢失）
_background_tasks: set = set()

def _track_task(coro):
    """创建并追踪后台任务，完成后自动移除"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task

async def _init_service_for_user(platform: str, user_id: int, account: str, password: str) -> bool:
    """Unified service initialization helper (CloudPets/PetKit only) — 加锁防并发覆盖"""
    try:
        logger.info(f"Initializing {platform} service for user {user_id}...")
        if platform == "petkit":
            async with state.petkit_lock:
                state.petkit = PetKitService(account, password, user_id=user_id)
                success = await state.petkit.initialize()
            logger.info(f"{'✓' if success else '⚠'} PetKit init {'success' if success else 'failed'}")
            return success
        elif platform == "cloudpets":
            async with state.cloudpets_lock:
                import backend.app.services.cloudpets_service as cp_module
                state.cloudpets = cp_module.CloudPetsService(user_id=user_id)
                success = await state.cloudpets.initialize(
                    account=account, password=password
                )
            logger.info(f"{'✓' if success else '⚠'} CloudPets init {'success' if success else 'failed'}")
            return success
        else:
            logger.warning(f"Unknown platform: {platform}")
            return False
    except Exception as e:
        logger.error(f"{platform} init failed: {e}")
        return False

async def _get_first_user_with_platform(platform: str) -> Optional[int]:
    """Query first user ID with specified platform config"""
    try:
        from sqlmodel import Session, select
        from .models.models import SystemConfig
        from .models.db import engine
        loop = asyncio.get_running_loop()
        def _query():
            with Session(engine) as session:
                stmt = select(SystemConfig.user_id).where(
                    SystemConfig.platform == platform, SystemConfig.key == "account"
                ).distinct()
                ids = session.exec(stmt).all()
                return ids[0] if ids else None
        return await loop.run_in_executor(None, _query)
    except Exception as e:
        logger.warning(f"Query {platform} user failed: {e}")
        return None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app startup and shutdown"""
    start_time = time.time()
    logger.info("=== Starting application ===")

    init_db()
    logger.info(f"✓ DB initialized in {time.time() - start_time:.2f}s")

    # 加载全局运行时配置到缓存（systemconfig → 内存，DB 缺失则回退环境变量）
    await _load_global_configs()

    # 加载设备缓存（启动时全量加载，后续按需刷新）
    from .utils.device_cache import device_cache
    await device_cache.load_all()

    # 并行初始化所有服务（减少总启动时间）
    logger.info("Initializing services...")
    svc_start = time.time()

    # 并行获取配置
    from .utils.config_manager import get_configs_batch
    config_queries = [
        ("account", None, "cloudpets"),
        ("password", None, "cloudpets"),
        ("account", None, "petkit"),
        ("password", None, "petkit"),
    ]
    configs = await get_configs_batch(config_queries)

    # CloudPets 初始化
    cp_account = configs.get("account_0_cloudpets")
    cp_password = configs.get("password_0_cloudpets")
    if cp_account and cp_password:
        first_user_id = await _get_first_user_with_platform("cloudpets")
        if first_user_id:
            success = await _init_service_for_user("cloudpets", first_user_id, cp_account, cp_password)
            if not success:
                state.cloudpets = None
        else:
            await cloudpets_service.initialize()
            state.cloudpets = cloudpets_service
    else:
        await cloudpets_service.initialize()
        state.cloudpets = cloudpets_service

    # PetKit 初始化
    pk_account = configs.get("account_0_petkit")
    pk_password = configs.get("password_0_petkit")
    if pk_account and pk_password:
        petkit_user_id = await _get_first_user_with_platform("petkit")
        if petkit_user_id:
            state.petkit = PetKitService(pk_account, pk_password, user_id=petkit_user_id)
            try:
                await state.petkit.initialize()
            except Exception as e:
                logger.error(f"PetKit connection failed: {e}")
                state.petkit = None
        else:
            state.petkit = PetKitService(pk_account, pk_password)
            try:
                await state.petkit.initialize()
            except Exception as e:
                logger.error(f"PetKit connection failed: {e}")
                state.petkit = None

    logger.info(f"✓ Services initialized in {time.time() - svc_start:.2f}s")

    # Init AppState
    state.data_refresh_task = None

    # 启动时清理脏数据：user_id!=0 且 key 不在允许集合中的记录
    try:
        from .models.models import SystemConfig
        from sqlmodel import Session, select
        from .models.db import engine

        VALID_USER_KEYS = {'account', 'password', 'token', 'ble_address'}
        def _cleanup():
            with Session(engine) as session:
                dirty = session.exec(
                    select(SystemConfig).where(
                        SystemConfig.user_id != 0,
                        SystemConfig.is_active == True,
                        SystemConfig.key.notin_(VALID_USER_KEYS),
                    )
                ).all()
                for rec in dirty:
                    rec.is_active = False
                    session.add(rec)
                if dirty:
                    session.commit()
                    logger.info(f"🧹 清理了 {len(dirty)} 条脏数据记录 (user_id!=0, key不在允许集合)")
                else:
                    logger.debug("无脏数据需要清理")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _cleanup)
    except Exception as e:
        logger.warning(f"启动时清理脏数据失败（非致命）: {e}")

    # Add scheduled tasks
    async def refresh_dashboard_cache():
        """后台定期刷新所有用户的仪表板缓存（无感刷新 + token 双写）"""
        try:
            from .utils.device_cache import device_cache
            from sqlmodel import Session, select
            from .models.models import SystemConfig
            from .models.db import engine

            logger.info("Starting background dashboard cache refresh...")
            start_time = time.time()

            # 获取所有有配置的用户ID
            loop = asyncio.get_running_loop()

            def _get_user_ids():
                with Session(engine) as session:
                    own_stmt = select(SystemConfig.user_id).where(
                        SystemConfig.platform.in_(["petkit", "cloudpets"]),
                        SystemConfig.key == "account",
                        SystemConfig.is_active == True
                    ).distinct()
                    own_ids = session.exec(own_stmt).all()
                    from .models.models import SharedDeviceConfig
                    shared_stmt = select(SharedDeviceConfig.to_user_id).distinct()
                    shared_ids = session.exec(shared_stmt).all()
                    all_ids = set(own_ids) | set(shared_ids)
                    return list(all_ids)

            user_ids = await loop.run_in_executor(None, _get_user_ids)

            if not user_ids:
                logger.debug("No users with platform config found")
                return

            logger.info(f"Refreshing cache for {len(user_ids)} users: {user_ids}")

            async def refresh_single_user(uid):
                try:
                    cache_prefix = f'user_{uid}'
                    await cache_manager.delete(f'{cache_prefix}_dashboard_combined_data')

                    # 从 DeviceCache 获取设备配置（避免重复查 DB）
                    user_platforms = await device_cache.get_user_platforms(uid)
                    pk_rec = user_platforms.get('petkit')
                    cp_rec = user_platforms.get('cloudpets')

                    has_petkit = pk_rec is not None and bool(pk_rec.account and pk_rec.password)
                    has_cloudpets = cp_rec is not None and bool(cp_rec.account and cp_rec.password)

                    shared_creds = await _get_shared_platform_credentials(uid)
                    if 'petkit' in shared_creds and not has_petkit:
                        has_petkit = True
                    if 'cloudpets' in shared_creds and not has_cloudpets:
                        has_cloudpets = True

                    if not has_petkit and not has_cloudpets:
                        # 仅 BLE 共享设备（xiaomi）时仍需渲染列表，不提前返回
                        has_shared_ble = any(
                            cred_info.get('_is_ble') for cred_info in (shared_creds or {}).values()
                        )
                        if not has_shared_ble:
                            await cache_manager.set(
                                f'{cache_prefix}_dashboard_combined_data',
                                {'petkit_devices': [], 'litterbox_stats': {},
                                 'cloudpets_servings': {}, 'cloudpets_plans': [],
                                 'has_shared_devices': False, 'device_platforms': list(user_platforms.values())},
                                ttl=120
                            )
                            return

                    petkit_devices, servings, plans = [], {}, []
                    from .utils.config_manager import update_cloud_device_token

                    if has_petkit:
                        pk_service, pk_is_temp = await _get_petkit_for_user(uid)
                        if pk_service:
                            try:
                                petkit_devices = await pk_service.get_devices()
                                # 【双写】token 刷新后写回设备配置组
                                mem_session = getattr(pk_service, '_memory_session', None)
                                if mem_session and pk_rec:
                                    import json as _json
                                    token_str = _json.dumps(mem_session)
                                    await update_cloud_device_token(uid, 'petkit', token_str, pk_rec.device_name)
                            finally:
                                await _release_service(pk_service, pk_is_temp)
                        await cache_manager.set(f'{cache_prefix}_petkit_devices', petkit_devices, ttl=300)

                    if has_cloudpets:
                        cp_service, cp_is_temp = await _get_cloudpets_for_user(uid)
                        if cp_service:
                            try:
                                servings = await cp_service.get_servings_today() or {}
                                plans = await cp_service.get_feeding_plans() or []
                                # 【双写】token 刷新后写回设备配置组
                                mem_token = getattr(cp_service, '_memory_token', None)
                                if mem_token and cp_rec:
                                    await update_cloud_device_token(uid, 'cloudpets', mem_token, cp_rec.device_name)
                            finally:
                                await _release_service(cp_service, cp_is_temp)
                        await cache_manager.set(f'{cache_prefix}_cloudpets_servings', servings, ttl=120)
                        await cache_manager.set(f'{cache_prefix}_cloudpets_plans', plans, ttl=300)

                    if petkit_devices and has_petkit:
                        pk_service, pk_is_temp = await _get_petkit_for_user(uid)
                        if pk_service:
                            try:
                                for device in petkit_devices:
                                    if hasattr(device, 'id'):
                                        cache_key = f'{cache_prefix}_petkit_stats_{device.id}'
                                        stats = await pk_service.get_daily_stats(device.id)
                                        await cache_manager.set(cache_key, stats, ttl=180)
                            finally:
                                await _release_service(pk_service, pk_is_temp)

                    # 体脂秤统计
                    scale_stats = {'today_count': 0, 'latest_body_fat': None}
                    try:
                        from datetime import datetime, timedelta
                        from .models.models import WeightRecord
                        from .models.db import engine
                        from sqlalchemy import func

                        def _query_scale_stats():
                            with Session(engine) as db_session:
                                today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
                                today_end = int(datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999).timestamp() * 1000)
                                count_stmt = select(func.count()).select_from(WeightRecord).where(
                                    WeightRecord.user_id == uid,
                                    WeightRecord.timestamp >= today_start,
                                    WeightRecord.timestamp <= today_end
                                )
                                today_count = db_session.exec(count_stmt).one()
                                latest_stmt = select(WeightRecord.body_fat).where(
                                    WeightRecord.user_id == uid,
                                    WeightRecord.timestamp >= today_start,
                                    WeightRecord.timestamp <= today_end,
                                    WeightRecord.body_fat.isnot(None)
                                ).order_by(WeightRecord.timestamp.desc()).limit(1)
                                latest_body_fat_raw = db_session.exec(latest_stmt).first()
                                return {'today_count': today_count, 'latest_body_fat': round(latest_body_fat_raw, 1) if latest_body_fat_raw else None}
                        scale_stats = await loop.run_in_executor(None, _query_scale_stats)
                    except Exception as e:
                        logger.error(f'[Refresh] 体脂秤统计失败: {e}')

                    # 刷新 DeviceCache（token 已更新）
                    await device_cache.invalidate_user(uid)

                    # 刷新 DeviceCache 后重新获取 device_platforms
                    user_platforms = await device_cache.get_user_platforms(uid)
                    # 构建设备平台列表（自有设备）
                    device_platforms = [
                        {'platform': p, 'device_name': r.device_name, 'device_key': r.device_key,
                         'is_ble': r.is_ble, 'is_complete': r.is_complete, 'is_shared': False}
                        for p, r in user_platforms.items()
                    ]
                    # 【修复】补充共享设备平台（SharedDeviceConfig 不在 DeviceCache 中）
                    existing_plats = {p['platform'] for p in device_platforms}
                    for plat_name, cred_info in (shared_creds or {}).items():
                        if plat_name not in existing_plats:
                            is_ble = cred_info.get('_is_ble', False)
                            device_platforms.append({
                                'platform': plat_name,
                                'device_name': f'shared_{plat_name}',
                                'device_key': f'{plat_name}_shared',
                                'is_ble': is_ble,
                                'is_complete': not is_ble,
                                'is_shared': True,
                            })
                    dashboard_data = {
                        'petkit_devices': petkit_devices,
                        'litterbox_stats': {},
                        'cloudpets_servings': servings,
                        'cloudpets_plans': plans,
                        'scale_stats': scale_stats,
                        'has_shared_devices': any(p in (shared_creds or {}) for p in ['petkit', 'cloudpets', 'xiaomi']),
                        'device_platforms': device_platforms,
                        'xiaomi_config': any(
                            r.is_ble and r.is_complete for r in user_platforms.values()
                        ),
                    }
                    await cache_manager.set(f'{cache_prefix}_dashboard_combined_data', dashboard_data, ttl=120)
                    logger.info(f"✓ Cache refreshed for user {uid}")
                except Exception as e:
                    logger.error(f"Failed to refresh cache for user {uid}: {e}")

            await asyncio.gather(*[refresh_single_user(uid) for uid in user_ids], return_exceptions=True)
            elapsed = time.time() - start_time
            logger.info(f"Dashboard cache refresh completed in {elapsed:.2f}s")
        except Exception as e:
            logger.error(f"Dashboard cache refresh failed: {e}")

    # 每90秒刷新一次缓存（比缓存过期时间60s长，确保在过期前刷新）
    await scheduler.add_task('dashboard_cache_refresh', refresh_dashboard_cache, interval=90, immediate=False)

    # 后台定期清理过期分享（每5分钟执行一次）
    async def cleanup_expired_shares():
        """自动撤销所有已过期的分享"""
        try:
            from .models.models import DeviceShare
            from .models.db import engine
            from sqlmodel import Session, select
            loop = asyncio.get_running_loop()
            def _cleanup():
                with Session(engine) as s:
                    now_ms = int(time.time() * 1000)
                    expired = s.exec(
                        select(DeviceShare).where(
                            DeviceShare.status.in_(["accepted", "pending"]),
                            DeviceShare.expires_at <= now_ms,
                        )
                    ).all()
                    if not expired:
                        return 0, []
                    to_users = set()
                    for share in expired:
                        share.status = "revoked"
                        s.add(share)
                        if share.to_user_id:
                            to_users.add(share.to_user_id)
                    s.commit()
                    return len(expired), list(to_users)
            count, to_users = await loop.run_in_executor(None, _cleanup)
            if count:
                logger.info(f"⏰ 后台清理了 {count} 个过期分享")
                from .utils.device_cache import device_cache
                for uid in to_users:
                    await device_cache.invalidate_user(uid)
        except Exception as e:
            logger.error(f"Expired share cleanup failed: {e}")

    await scheduler.add_task('expired_share_cleanup', cleanup_expired_shares, interval=300, immediate=True)
    await scheduler.start()

    # CacheManager 为惰性过期，无需后台清理

    logger.info(f"=== App initialized in {time.time() - start_time:.2f}s ===")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await scheduler.stop()
    if state.petkit:
        await state.petkit.close()
    if state.cloudpets:
        await state.cloudpets.close()

# --- App Config ---
app = FastAPI(title="Smart Home Controller", version="0.3.0", lifespan=lifespan)
app.include_router(share_router)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_petkit():
    """Get logged-in PetKit instance"""
    if not state.petkit:
        raise HTTPException(status_code=503, detail="PetKit service not initialized")
    return state.petkit


# --- 公共服务获取工具（消除重复代码） ---
async def _get_user_credentials(user_id: int, platform: str):
    """获取用户指定平台的账号密码，返回 (account, password) 或 (None, None)
    【修复】自有凭据不足时回退到 SharedDeviceConfig 共享凭据"""
    from .utils.config_manager import get_configs_batch
    configs = await get_configs_batch([
        ("account", user_id, platform),
        ("password", user_id, platform),
    ])
    account = configs.get(f"account_{user_id}_{platform}")
    password = configs.get(f"password_{user_id}_{platform}")

    if account and password:
        return account, password

    # 【修复】共享凭据回退
    shared_creds = await _get_shared_platform_credentials(user_id)
    if platform in shared_creds:
        logger.info(f"[Credentials] 用户 {user_id} 使用共享的 {platform} 凭据")
        return shared_creds[platform]["account"], shared_creds[platform]["password"]

    return account, password


async def _get_petkit_for_user(user_id: int):
    """获取 PetKit 服务实例（优先复用全局，否则创建临时）
    返回 (service, is_temp) 元组，调用方需在 is_temp=True 时手动 close
    """
    if state.petkit and getattr(state.petkit, 'user_id', None) == user_id:
        return state.petkit, False
    account, password = await _get_user_credentials(user_id, "petkit")
    if not account or not password:
        return None, False
    temp = PetKitService(account, password, user_id=user_id)
    await temp.initialize()
    return temp, True


async def _get_cloudpets_for_user(user_id: int):
    """获取 CloudPets 服务实例（优先复用全局，否则创建临时）
    【修复】获取凭据时回退到共享凭据
    返回 (service, is_temp) 元组
    """
    if state.cloudpets and getattr(state.cloudpets, 'user_id', None) == user_id:
        return state.cloudpets, False

    account, password = await _get_user_credentials(user_id, "cloudpets")
    if not account or not password:
        return None, False

    import backend.app.services.cloudpets_service as cp_module
    temp = cp_module.CloudPetsService(user_id=user_id)
    await temp.initialize(account=account, password=password)
    return temp, True


async def _release_service(service, is_temp):
    """安全释放临时服务"""
    if is_temp and service and hasattr(service, 'close'):
        try:
            await service.close()
        except Exception:
            pass

# 静态页面路由已全部移除（前端使用微信小程序原生页面）
# --- Cache & Dashboard APIs ---
@app.get("/api/cache/status")
async def cache_status():
    """获取缓存状态和任务统计"""
    try:
        # 获取任务调度器统计
        task_stats = scheduler.get_task_stats()

        return {
            "cache_size": await cache_manager.size(),
            "last_refresh": await cache_manager.get('dashboard_last_refresh'),
            "scheduled_tasks": task_stats
        }
    except Exception as e:
        logger.error(f"Failed to get cache status: {e}")
        return {"error": str(e)}

@app.post("/api/cache/refresh")
async def force_refresh_cache(user_id: Optional[int] = None):
    """强制刷新指定用户的缓存数据"""
    try:
        if not user_id:
            return {"status": "error", "message": "请提供 user_id"}

        # 清除该用户的所有缓存
        cache_prefix = f'user_{user_id}'
        await cache_manager.delete(f'{cache_prefix}_dashboard_combined_data')
        await cache_manager.delete(f'{cache_prefix}_petkit_devices')
        await cache_manager.delete(f'{cache_prefix}_cloudpets_servings')
        await cache_manager.delete(f'{cache_prefix}_cloudpets_plans')
        # 【新增】清除体脂秤统计相关缓存（如果有）
        await cache_manager.delete(f'{cache_prefix}_scale_stats')

        logger.info(f"Cache refreshed for user {user_id}")
        return {"status": "success", "message": "缓存已清除，下次访问将重新加载"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新失败: {str(e)}")

@app.post("/api/cache/clear")
async def clear_all_cache():
    """Clear all cached data (for debugging)"""
    try:
        await cache_manager.clear()
        logger.info("All cache cleared")
        return {"status": "success", "message": "缓存已清空"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理缓存失败: {str(e)}")


async def _get_shared_platform_credentials(user_id: int) -> dict:
    """
    【新增】查询该用户接受的共享设备分享者的原始凭据
    返回: { "petkit": {"account": "real_account", "password": "real_password"}, ... }
    如果用户没有接受的分享，返回空字典
    """
    try:
        from .models.models import DeviceShare, SharedDeviceConfig, SystemConfig

        def _query():
            with Session(engine) as session:
                # 查该用户已接受的分享记录
                shares = session.exec(
                    select(DeviceShare).where(
                        DeviceShare.to_user_id == user_id,
                        DeviceShare.status == "accepted"
                    )
                ).all()

                if not shares:
                    return {}

                # 收集所有分享者的 platform
                platform_set = set()
                for share in shares:
                    device_keys = json.loads(share.device_keys) if share.device_keys else []
                    for dk in device_keys:
                        parts = dk.split('_', 1)
                        if parts:
                            platform_set.add(parts[0])

                if not platform_set:
                    return {}

                # 获取分享者们的原始凭据（从多个分享者中汇总）
                result = {}
                for platform in platform_set:
                    # 找最近接受的分享中该平台的凭据
                    shared_configs = session.exec(
                        select(SharedDeviceConfig).where(
                            SharedDeviceConfig.to_user_id == user_id,
                            SharedDeviceConfig.platform == platform
                        ).order_by(SharedDeviceConfig.id.desc())
                    ).all()

                    if not shared_configs:
                        continue

                    # 取最新的分享记录，然后读取分享者的原始凭据
                    latest_shared = shared_configs[0]
                    # 找到对应的分享记录获取 from_user_id
                    share_record = session.get(DeviceShare, latest_shared.share_id)
                    if not share_record:
                        continue

                    # 【时效检查】如果分享已过期，跳过并不再返回凭据
                    now_ms = int(time.time() * 1000)
                    if share_record.status != "accepted" or now_ms > share_record.expires_at:
                        # 自动标记为已过期
                        if share_record.status == "accepted":
                            share_record.status = "revoked"
                            session.add(share_record)
                            session.commit()
                        continue

                    # 读取分享者的原始凭据（解密）
                    from_user_id = share_record.from_user_id
                    config_rows = session.exec(
                        select(SystemConfig).where(
                            SystemConfig.user_id == from_user_id,
                            SystemConfig.key.in_(["account", "password"]),
                            SystemConfig.platform == platform,
                            SystemConfig.is_active == True
                        )
                    ).all()

                    account_val = None
                    password_val = None
                    for cfg in config_rows:
                        val = ConfigEncryptor.decrypt(cfg.value) if cfg.is_encrypted else cfg.value
                        if cfg.key == "account":
                            account_val = val
                        elif cfg.key == "password":
                            password_val = val

                    if account_val and password_val:
                        result[platform] = {"account": account_val, "password": password_val}
                    # BLE 平台（xiaomi）无需凭据，标记即可
                    elif platform in ('xiaomi',):
                        result[platform] = {"account": '', "password": '', '_is_ble': True}

                return result

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _query)
    except Exception as e:
        logger.error(f"Failed to get shared platform credentials for user {user_id}: {e}")
        return {}


@app.get("/api/dashboard/data")
async def get_dashboard_data(user_id: Optional[int] = None, session: Session = Depends(get_session)):
    """Get aggregated dashboard data (cached, user-specific) - 优化版：并行执行+批量配置"""
    try:
        # 提前导入，避免作用域问题
        from .utils.config_manager import get_config_from_db, get_configs_batch

        # If no user_id provided, try to get from first configured user
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets") or await _get_first_user_with_platform("petkit")
            if not user_id:
                return {"petkit_devices": [], "litterbox_stats": {}, "cloudpets_servings": {}, "cloudpets_plans": []}

        cache_prefix = f'user_{user_id}'
        cached_data = await cache_manager.get(f'{cache_prefix}_dashboard_combined_data')
        if cached_data:
            return cached_data

        # 从 DeviceCache 获取该用户的设备平台配置
        from .utils.device_cache import device_cache
        user_platforms = await device_cache.get_user_platforms(user_id)

        petkit_rec = user_platforms.get('petkit')
        cloudpets_rec = user_platforms.get('cloudpets')
        xiaomi_rec = user_platforms.get('xiaomi')

        petkit_username = petkit_rec.account if petkit_rec and petkit_rec.is_complete else None
        petkit_password = petkit_rec.password if petkit_rec and petkit_rec.is_complete else None
        cloudpets_account = cloudpets_rec.account if cloudpets_rec and cloudpets_rec.is_complete else None
        cloudpets_password = cloudpets_rec.password if cloudpets_rec and cloudpets_rec.is_complete else None

        # 查询该用户接受的共享设备，补充没有自有配置的平台
        shared_creds = await _get_shared_platform_credentials(user_id)
        if 'petkit' in shared_creds and not petkit_username:
            petkit_username = shared_creds['petkit']['account']
            petkit_password = shared_creds['petkit']['password']
            logger.info(f"[Dashboard] 用户 {user_id} 使用共享的 PetKit 凭证")
        if 'cloudpets' in shared_creds and not cloudpets_account:
            cloudpets_account = shared_creds['cloudpets']['account']
            cloudpets_password = shared_creds['cloudpets']['password']
            logger.info(f"[Dashboard] 用户 {user_id} 使用共享的 CloudPets 凭证")

        dashboard_data = {}

        # 设备平台列表（供前端渲染设备卡片）— 从 DeviceCache 获取自有设备
        dashboard_data['device_platforms'] = [
            {
                'platform': p,
                'device_name': r.device_name,
                'device_key': r.device_key,
                'is_ble': r.is_ble,
                'is_complete': r.is_complete,
                'is_shared': False,
            }
            for p, r in user_platforms.items()
        ]

        # 【修复】将共享设备平台也注入 device_platforms（SharedDeviceConfig 不在 DeviceCache 中）
        existing_platforms = {p['platform'] for p in dashboard_data['device_platforms']}
        for platform_name, cred_info in shared_creds.items():
            if platform_name not in existing_platforms:
                is_ble = cred_info.get('_is_ble', False)
                dashboard_data['device_platforms'].append({
                    'platform': platform_name,
                    'device_name': f'shared_{platform_name}',
                    'device_key': f'{platform_name}_shared',
                    'is_ble': is_ble,
                    'is_complete': not is_ble,  # BLE 设备在 B 处无法连接，标记为不完整
                    'is_shared': True,
                })

        dashboard_data['has_shared_devices'] = len(shared_creds) > 0

        # 并行获取PetKit设备和CloudPets数据
        async def fetch_petkit_devices():
            """获取PetKit设备列表（含 token 双写）"""
            petkit_devices = await cache_manager.get(f'{cache_prefix}_petkit_devices')
            if petkit_devices:
                return petkit_devices

            if not petkit_username or not petkit_password:
                return []

            if state.petkit and getattr(state.petkit, 'user_id', None) == user_id:
                pk_svc = state.petkit
                devices = await pk_svc.get_devices()
                # 【双写】token 刷新后写回设备配置组
                mem_session = getattr(pk_svc, '_memory_session', None)
                if mem_session and petkit_rec:
                    from .utils.config_manager import update_cloud_device_token
                    await update_cloud_device_token(user_id, 'petkit', json.dumps(mem_session), petkit_rec.device_name)
            else:
                temp_service = PetKitService(petkit_username, petkit_password, user_id=user_id)
                await temp_service.initialize()
                devices = await temp_service.get_devices()
                mem_session = getattr(temp_service, '_memory_session', None)
                if mem_session and petkit_rec:
                    from .utils.config_manager import update_cloud_device_token
                    await update_cloud_device_token(user_id, 'petkit', json.dumps(mem_session), petkit_rec.device_name)
                await temp_service.close()

            await cache_manager.set(f'{cache_prefix}_petkit_devices', devices, ttl=300)
            return devices

        async def fetch_cloudpets_data():
            """获取CloudPets数据（servings + plans，含 token 双写）"""
            result = {"servings": {}, "plans": []}

            if not cloudpets_account or not cloudpets_password:
                return result

            if state.cloudpets and getattr(state.cloudpets, 'user_id', None) == user_id:
                cp_svc = state.cloudpets
                servings = await cp_svc.get_servings_today()
                plans = await cp_svc.get_feeding_plans()
                mem_token = getattr(cp_svc, '_memory_token', None)
                if mem_token and cloudpets_rec:
                    from .utils.config_manager import update_cloud_device_token
                    await update_cloud_device_token(user_id, 'cloudpets', mem_token, cloudpets_rec.device_name)
            else:
                import backend.app.services.cloudpets_service as cp_module
                temp_service = cp_module.CloudPetsService(user_id=user_id)
                await temp_service.initialize()
                servings = await temp_service.get_servings_today()
                plans = await temp_service.get_feeding_plans()
                mem_token = getattr(temp_service, '_memory_token', None)
                if mem_token and cloudpets_rec:
                    from .utils.config_manager import update_cloud_device_token
                    await update_cloud_device_token(user_id, 'cloudpets', mem_token, cloudpets_rec.device_name)
                await temp_service.close()

            await cache_manager.set(f'{cache_prefix}_cloudpets_servings', servings, ttl=120)
            await cache_manager.set(f'{cache_prefix}_cloudpets_plans', plans, ttl=300)
            result["servings"] = servings or {}
            result["plans"] = plans or []
            return result

        # 并行执行独立的外部API调用
        petkit_devices, cloudpets_data = await asyncio.gather(
            fetch_petkit_devices(),
            fetch_cloudpets_data(),
            return_exceptions=True
        )

        # 处理异常
        if isinstance(petkit_devices, Exception):
            logger.error(f"Failed to fetch PetKit devices: {petkit_devices}")
            petkit_devices = []
        if isinstance(cloudpets_data, Exception):
            logger.error(f"Failed to fetch CloudPets data: {cloudpets_data}")
            cloudpets_data = {"servings": {}, "plans": []}

        dashboard_data['petkit_devices'] = petkit_devices or []
        dashboard_data['cloudpets_servings'] = cloudpets_data.get('servings', {})
        dashboard_data['cloudpets_plans'] = cloudpets_data.get('plans', [])

        # token 可能已更新，刷新 DeviceCache
        from .utils.device_cache import device_cache as _dc
        await _dc.invalidate_user(user_id)

        # 体脂秤配置（来自缓存记录）
        dashboard_data['xiaomi_config'] = xiaomi_rec is not None and xiaomi_rec.is_complete if xiaomi_rec else False

        # 获取体脂秤统计数据
        try:
            from datetime import datetime, timedelta
            from .models.models import WeightRecord
            from sqlmodel import select

            # 计算今天的开始和结束时间戳（毫秒）
            today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            today_end = int(datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999).timestamp() * 1000)

            # 查询今日测量次数
            stmt_today_count = select(WeightRecord).where(
                WeightRecord.user_id == user_id,
                WeightRecord.timestamp >= today_start,
                WeightRecord.timestamp <= today_end
            ).order_by(WeightRecord.timestamp.desc())
            today_records = session.exec(stmt_today_count).all()
            today_count = len(today_records)

            # 查询最新一次测量的体脂率
            latest_body_fat = None
            if today_records:
                latest_record = today_records[0]
                if latest_record.body_fat:
                    latest_body_fat = round(latest_record.body_fat, 1)

            scale_stats = {
                'today_count': today_count,
                'latest_body_fat': latest_body_fat
            }

            logger.info(f'[Dashboard] 体脂秤统计 - 今日测量: {today_count}, 最新体脂: {latest_body_fat}')
            dashboard_data['scale_stats'] = scale_stats
        except Exception as e:
            logger.error(f'[Dashboard] 获取体脂秤统计失败: {e}')
            dashboard_data['scale_stats'] = {'today_count': 0, 'latest_body_fat': None}

        await cache_manager.set(f'{cache_prefix}_dashboard_combined_data', dashboard_data, ttl=120)
        return dashboard_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取仪表板数据失败：{str(e)}")
# --- PetKit APIs ---
@app.get("/api/petkit/devices")
async def petkit_devices(user_id: Optional[int] = None):
    """Get PetKit devices for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("petkit")
            if not user_id:
                return []

        cache_key = f'user_{user_id}_petkit_devices'
        cached_devices = await cache_manager.get(cache_key)
        if cached_devices:
            return cached_devices

        service, is_temp = await _get_petkit_for_user(user_id)
        if not service:
            return []

        try:
            devices = await service.get_devices()
        finally:
            await _release_service(service, is_temp)

        await cache_manager.set(cache_key, devices, ttl=300)
        return devices
    except Exception as e:
        logger.error(f"Failed to fetch PetKit devices: {e}")
        return []

@app.post("/api/petkit/clean")
async def petkit_clean(user_id: Optional[int] = None):
    """Clean litterbox for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("petkit")
            if not user_id:
                raise HTTPException(status_code=503, detail="PetKit service not configured")

        service, is_temp = await _get_petkit_for_user(user_id)
        if not service:
            raise HTTPException(status_code=503, detail="PetKit credentials missing")

        try:
            return await service.clean_litterbox(None)
        finally:
            await _release_service(service, is_temp)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Action failed: {str(e)}")

@app.post("/api/petkit/deodorize")
async def petkit_deodorize(user_id: Optional[int] = None):
    """Deodorize litterbox for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("petkit")
            if not user_id:
                raise HTTPException(status_code=503, detail="PetKit service not configured")

        service, is_temp = await _get_petkit_for_user(user_id)
        if not service:
            raise HTTPException(status_code=503, detail="PetKit credentials missing")

        try:
            return await service.deodorize_litterbox(None)
        finally:
            await _release_service(service, is_temp)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/petkit/stats")
async def petkit_daily_stats(device_id: Optional[str] = None, user_id: Optional[int] = None):
    """Get daily stats (accurate data, user-specific)"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("petkit")
            if not user_id:
                raise HTTPException(status_code=503, detail="PetKit service not configured")

        username, password = await _get_user_credentials(user_id, "petkit")

        if not username or not password:
            raise HTTPException(status_code=503, detail="PetKit credentials missing")

        if device_id == "null" or device_id == "":
            device_id = None

        cache_key = f'user_{user_id}_petkit_stats_{device_id or "default"}'
        cached_stats = await cache_manager.get(cache_key)
        if cached_stats:
            return cached_stats

        # Use existing service or create temp one
        if state.petkit and getattr(state.petkit, 'user_id', None) == user_id:
            service = state.petkit
        else:
            service = PetKitService(username, password, user_id=user_id)
            await service.initialize()

        stats = await service.get_daily_stats(device_id)

        # Close temp service if created
        if not (state.petkit and getattr(state.petkit, 'user_id', None) == user_id):
            await service.close()

        await cache_manager.set(cache_key, stats, ttl=180)
        return stats
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计数据失败：{str(e)}")

@app.get("/api/petkit/history")
async def petkit_history_stats(device_id: Optional[str] = None, days: int = 7, user_id: Optional[int] = None):
    """Get historical stats (user-specific)"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("petkit")
            if not user_id:
                raise HTTPException(status_code=503, detail="PetKit service not configured")

        username, password = await _get_user_credentials(user_id, "petkit")

        if not username or not password:
            raise HTTPException(status_code=503, detail="PetKit credentials missing")

        # Use existing service or create temp one
        if state.petkit and getattr(state.petkit, 'user_id', None) == user_id:
            service = state.petkit
        else:
            service = PetKitService(username, password, user_id=user_id)
            await service.initialize()

        result = await service.get_device_stats(device_id, days)

        # Close temp service if created
        if not (state.petkit and getattr(state.petkit, 'user_id', None) == user_id):
            await service.close()

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史统计失败: {str(e)}")

@app.get("/api/petkit/devices-stats")
async def petkit_devices_with_stats(user_id: Optional[int] = None):
    """Get devices with stats (cached, user-specific)"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("petkit")
            if not user_id:
                raise HTTPException(status_code=503, detail="PetKit service not configured")

        cache_key = f'user_{user_id}_petkit_devices_with_stats'
        cached_data = await cache_manager.get(cache_key)
        if cached_data:
            return cached_data

        # Initialize service for this user if needed
        username, password = await _get_user_credentials(user_id, "petkit")

        if not username or not password:
            raise HTTPException(status_code=503, detail="PetKit credentials missing")

        # Use existing service or create temp one
        if state.petkit and getattr(state.petkit, 'user_id', None) == user_id:
            service = state.petkit
        else:
            service = PetKitService(username, password, user_id=user_id)
            await service.initialize()

        devices = await service.get_devices()
        result = []
        for device in devices:
            device_id = getattr(device, 'id', '') if hasattr(device, 'id') else ''
            if device_id:
                stats_cache_key = f'user_{user_id}_petkit_stats_{device_id}'
                stats = await cache_manager.get(stats_cache_key)
                if not stats:
                    stats = await service.get_daily_stats(device_id)
                    await cache_manager.set(stats_cache_key, stats, ttl=60)

                device_dict = device if isinstance(device, dict) else {
                    "id": device_id, "name": getattr(device, 'name', 'Unknown'),
                    "type": getattr(device, 'type', 'Unknown'), "data": getattr(device, 'data', {})
                }
                if isinstance(stats, dict):
                    existing_summary = device_dict.get('state_summary', {})
                    merged_summary = {**existing_summary, **stats}
                    device_dict['state_summary'] = merged_summary
                result.append(device_dict)
            else:
                result.append(device)

        # Close temp service if created
        if not (state.petkit and getattr(state.petkit, 'user_id', None) == user_id):
            await service.close()

        await cache_manager.set(cache_key, result, ttl=60)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取设备和统计数据失败：{str(e)}")

# --- CloudPets APIs ---
@app.get("/api/cloudpets/servings_today")
async def cloudpets_servings_today(user_id: Optional[int] = None):
    """Get today's servings for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets")
            if not user_id:
                return {"result": 0}

        cache_key = f'user_{user_id}_cloudpets_servings'
        cached = await cache_manager.get(cache_key)
        if cached:
            return cached

        service, is_temp = await _get_cloudpets_for_user(user_id)
        if not service:
            return {"result": 0}

        try:
            result = await service.get_servings_today()
        finally:
            await _release_service(service, is_temp)

        await cache_manager.set(cache_key, result, ttl=120)
        return result
    except Exception as e:
        logger.error(f"Failed to get servings: {e}")
        return {"result": 0}

@app.post("/api/cloudpets/feed")
async def cloudpets_manual_feed(amount: int = 1, user_id: Optional[int] = None):
    """Manual feed for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets")
            if not user_id:
                raise HTTPException(status_code=503, detail="CloudPets service not configured")

        service, is_temp = await _get_cloudpets_for_user(user_id)
        if not service:
            raise HTTPException(status_code=503, detail="CloudPets credentials missing")

        try:
            return await service.manual_feed(amount)
        finally:
            await _release_service(service, is_temp)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feed failed: {str(e)}")

@app.get("/api/cloudpets/plans", response_model=List[CloudPetsPlan])
async def cloudpets_get_plans(user_id: Optional[int] = None):
    """Get feeding plans for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets")
            if not user_id:
                return []

        cache_key = f'user_{user_id}_cloudpets_plans'
        cached = await cache_manager.get(cache_key)
        if cached:
            return cached

        service, is_temp = await _get_cloudpets_for_user(user_id)
        if not service:
            return []

        try:
            plans = await service.get_feeding_plans()
        finally:
            await _release_service(service, is_temp)

        await cache_manager.set(cache_key, plans, ttl=300)
        return plans
    except Exception as e:
        logger.error(f"Failed to get plans: {e}")
        return []

@app.post("/api/cloudpets/plans", response_model=CloudPetsPlan)
async def cloudpets_add_plan(plan: CloudPetsPlan, user_id: Optional[int] = None):
    """Add feeding plan for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets")
            if not user_id:
                raise HTTPException(status_code=503, detail="CloudPets service not configured")

        service, is_temp = await _get_cloudpets_for_user(user_id)
        if not service:
            raise HTTPException(status_code=503, detail="CloudPets credentials missing")

        try:
            result = await service.add_feeding_plan(plan)
        finally:
            await _release_service(service, is_temp)

        await cache_manager.delete(f'user_{user_id}_cloudpets_plans')
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Add plan failed: {str(e)}")

@app.put("/api/cloudpets/plans/{plan_id}", response_model=CloudPetsPlan)
async def cloudpets_update_plan(plan_id: str, plan: CloudPetsPlan, user_id: Optional[int] = None):
    """Update feeding plan for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets")
            if not user_id:
                raise HTTPException(status_code=503, detail="CloudPets service not configured")

        service, is_temp = await _get_cloudpets_for_user(user_id)
        if not service:
            raise HTTPException(status_code=503, detail="CloudPets credentials missing")

        try:
            result = await service.update_feeding_plan(plan_id, plan)
        finally:
            await _release_service(service, is_temp)

        await cache_manager.delete(f'user_{user_id}_cloudpets_plans')
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update plan failed: {str(e)}")

@app.delete("/api/cloudpets/plans/{plan_id}")
async def cloudpets_delete_plan(plan_id: str, user_id: Optional[int] = None):
    """Delete feeding plan for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets")
            if not user_id:
                raise HTTPException(status_code=503, detail="CloudPets service not configured")

        service, is_temp = await _get_cloudpets_for_user(user_id)
        if not service:
            raise HTTPException(status_code=503, detail="CloudPets credentials missing")

        try:
            result = await service.delete_feeding_plan(plan_id)
        finally:
            await _release_service(service, is_temp)

        await cache_manager.delete(f'user_{user_id}_cloudpets_plans')
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete plan failed: {str(e)}")

@app.get("/api/cloudpets/feeder/status")
async def cloudpets_feeder_status(user_id: Optional[int] = None):
    """Get feeder status for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("cloudpets")
            if not user_id:
                return {"status": "not_configured"}

        service, is_temp = await _get_cloudpets_for_user(user_id)
        if not service:
            return {"status": "not_configured"}

        try:
            return await service.get_feeder_status()
        finally:
            await _release_service(service, is_temp)
    except Exception as e:
        logger.error(f"Failed to get feeder status: {e}")
        return {"status": "error"}

# /api/users 端点已废弃，注册/登录请使用 /api/auth/bind







# @app.get("/api/scale/history/") + @app.post("/api/scale/record") 已废弃，BIA 计算已移至前端 scale.js

# --- Auth APIs ---
# UserLoginRequest / UserLoginResponse 已随 /api/auth/login 移除，请使用 BindLoginRequest / BindLoginResponse

@app.get("/api/auth/check-config")
async def check_user_config(user_id: str):
    """Check if user has devices configured"""
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    try:
        from .utils.config_manager import get_user_devices
        user_devices = await get_user_devices(uid)
        has_devices = len(user_devices) > 0
        return {"has_configured": has_devices, "device_count": len(user_devices),
                "message": f"已添加 {len(user_devices)} 个设备" if has_devices else "请先添加设备"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检查配置失败: {str(e)}")

# @app.post("/api/auth/reinit-services") 已废弃，使用按需初始化

# ============================================================================
# WeChat 小程序登录认证（OpenID 绑定 + 静默免密直登）
# ============================================================================

# --- 运行时动态配置缓存（启动时从 systemconfig 加载，支持运行时更新）---
_global_configs: dict = {}  # 所有运行时配置的全局缓存

# 需要从 systemconfig(user_id=0) 加载的配置键列表
_GLOBAL_CONFIG_KEYS = [
    "WECHAT_APPID",
    "WECHAT_SECRET",
    "TOKEN_EXPIRE_HOURS",
]


def _get_token_expire_ms() -> int:
    """
    从全局缓存获取 token 过期毫秒数
    若无缓存（如单元测试），回退默认 720 小时
    """
    hours_str = _global_configs.get("TOKEN_EXPIRE_HOURS") or "720"
    return int(hours_str) * 3600 * 1000


def generate_session_token() -> tuple[str, str, int]:
    """
    生成会话 token
    返回: (raw_token, token_hash, expires_at)
    - raw_token: 返回给前端（32字节hex，共64字符）
    - token_hash: SHA256(raw_token)，存入user表
    - expires_at: 毫秒时间戳
    """
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = int(time.time() * 1000) + _get_token_expire_ms()
    return raw_token, token_hash, expires_at


def hash_token(token: str) -> str:
    """对token做SHA256哈希（用于查询匹配）"""
    return hashlib.sha256(token.encode()).hexdigest()


async def get_current_user(
    authorization: Optional[str] = Header(None),
) -> User:
    """
    从 Authorization Header 解析当前用户（替换JWT中间件）
    用法: current_user: User = Depends(get_current_user)
    自动创建独立session，不影响调用方的session
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token为空")
    token_hash = hash_token(token)
    now = int(time.time() * 1000)
    with Session(engine) as s:
        user = s.exec(
            select(User).where(
                User.token_hash == token_hash,
                User.token_expires_at > now,
            )
        ).first()
        if not user:
            raise HTTPException(status_code=401, detail="Token无效或已过期")
        return user


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed: str) -> bool:
    """验证密码"""
    import bcrypt
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed.encode("utf-8"))


# --- 系统配置全局缓存：从 systemconfig(user_id=0) 加载，支持运行时刷新 ---
async def _load_global_configs():
    """
    启动时／刷新时从 systemconfig 加载所有全局配置到 _global_configs
    始终先读 DB，DB 缺失则回退环境变量（确保启动可用）
    """
    global _global_configs
    from .utils.config_manager import get_config_from_db

    loaded = {}
    for key in _GLOBAL_CONFIG_KEYS:
        db_val = await get_config_from_db(key, user_id=0)
        if db_val:
            loaded[key] = db_val.strip()
        else:
            # 回退：WECHAT_* 从环境变量读取（开发便利），TOKEN 用硬编码默认值
            if key == "TOKEN_EXPIRE_HOURS":
                loaded[key] = "720"
            else:
                env_val = os.getenv(key, "")
                if env_val:
                    loaded[key] = env_val.strip()

    _global_configs = loaded
    missing = [k for k in _GLOBAL_CONFIG_KEYS if k not in _global_configs]
    if missing:
        logger.warning(f"⚠ systemconfig+env 均缺失以下配置: {missing}")
    else:
        logger.info(f"✓ 全局配置已加载到缓存: {list(_global_configs.keys())}")


async def _ensure_global_config(key: str) -> str:
    """
    按需获取单个全局配置值（惰性兜底）
    若缓存为空则尝试刷新，失败抛 500
    """
    val = _global_configs.get(key)
    if val:
        return val
    await _load_global_configs()
    val = _global_configs.get(key)
    if not val:
        raise HTTPException(
            status_code=500,
            detail=f"系统配置 {key} 缺失，请检查 systemconfig 表或环境变量",
        )
    return val


async def wx_code2session(code: str) -> dict:
    """通过微信 code 换取 openid 和 session_key（配置来自 systemconfig 全局缓存）"""
    appid = await _ensure_global_config("WECHAT_APPID")
    secret = await _ensure_global_config("WECHAT_SECRET")

    import httpx
    import ssl

    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": appid,
        "secret": secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }

    # 云托管环境可能缺失系统 CA 证书，先尝试默认验证，失败则降级
    for attempt, verify in enumerate([True, False]):
        try:
            client_kwargs = {"timeout": 10.0}
            if not verify:
                client_kwargs["verify"] = False
                logger.warning("微信 API SSL 验证已禁用（云托管环境兼容模式）")
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(url, params=params)
                data = resp.json()
            break  # 成功则退出重试循环
        except (httpx.ConnectError, ssl.SSLError) as e:
            if attempt == 0:
                logger.warning(f"微信 API SSL 验证失败，尝试禁用验证重试: {e}")
                continue
            logger.error(f"微信 API 请求失败（已尝试禁用 SSL）: {e}")
            raise HTTPException(status_code=502, detail="微信服务请求失败（SSL 连接错误）")

    if "errcode" in data and data["errcode"] != 0:
        logger.error(f"微信 code2session 失败: {data}")
        raise HTTPException(status_code=400, detail=f"微信登录失败: {data.get('errmsg', '未知错误')}")

    return {
        "openid": data.get("openid", ""),
        "session_key": data.get("session_key", ""),
        "unionid": data.get("unionid"),
    }



# --- 接口 A：首次账密登录 + 绑定 OpenID ---
class BindLoginRequest(BaseModel):
    account: str       # 手机号
    password: str      # 明文密码
    code: str          # wx.login() 返回的临时 code
    force_bind: bool = False  # 是否强制改绑（忽略冲突直接覆盖）
    skip_bind: bool = False   # 是否跳过openid绑定（拒绝改绑时的登录，不更新User.openid）

class BindLoginResponse(BaseModel):
    token: str
    user_id: int
    phone_number: str
    openid: str
    nickname: Optional[str] = None
    is_new_user: bool = False
    openid_bound: bool = True  # 仅 skip_bind=True 时为 False，标识此登录未绑定openid

@app.post("/api/auth/bind", response_model=BindLoginResponse)
async def auth_bind_login(request: BindLoginRequest, session: Session = Depends(get_session)):
    """首次账密登录激活绑定"""
    account = request.account.strip()
    password = request.password.strip()
    code = request.code.strip()

    # --- 1. 参数校验 ---
    if not account or len(account) != 11 or not account.isdigit():
        raise HTTPException(status_code=400, detail="请输入正确的11位手机号")
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="密码长度不能少于4位")
    if not code:
        raise HTTPException(status_code=400, detail="缺少微信登录凭证")

    # --- 2. 向微信换取 openid ---
    wx_data = await wx_code2session(code)
    openid = wx_data["openid"]
    new_session_key = wx_data["session_key"]
    unionid = wx_data.get("unionid")

    if not openid:
        raise HTTPException(status_code=400, detail="获取微信 OpenID 失败")

    # --- 3. 查找或创建用户 ---
    user = session.exec(select(User).where(User.phone_number == account)).first()
    is_new_user = False

    if user:
        # 已有用户 → 校验密码
        if not user.password_hash:
            # 用户之前可能用手机号免密登录过，尚无密码 → 直接设置密码
            user.password_hash = hash_password(password)
            session.add(user)
            session.commit()
            session.refresh(user)
            logger.info(f"用户 {account} 首次设置密码")
        elif not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="手机号或密码错误")
    else:
        # 新用户注册
        is_new_user = True
        user = User(
            phone_number=account,
            password_hash=hash_password(password),
            nickname=f"用户{account[-4:]}",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info(f"新用户注册: {account}, ID: {user.id}")

    # --- 3.5 记录隐私协议同意时间 ---
    if not user.privacy_consent_at:
        user.privacy_consent_at = int(time.time() * 1000)

    # --- 4. 设备绑定冲突检测 ---
    # 此 openid 是否已绑定到其他用户？
    old_user = session.exec(
        select(User).where(User.openid == openid, User.id != user.id)
    ).first()
    has_conflict = old_user is not None

    if has_conflict and not request.force_bind and not request.skip_bind:
        # 冲突且未指定策略 → 返回 409 让前端弹窗确认
        masked_phone = old_user.phone_number
        if masked_phone and len(masked_phone) >= 7:
            masked_phone = masked_phone[:3] + '****' + masked_phone[-4:]
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_BOUND",
                "message": "当前设备已绑定其他账号",
                "bound_user": {
                    "phone_masked": masked_phone,
                    "nickname": old_user.nickname or '',
                },
            }
        )

    # --- 5. 建立 / 更新 OpenID 绑定 ---
    if request.skip_bind:
        # 用户拒绝改绑：正常登录但不更新 openid 绑定
        logger.info(f"用户 {user.id} 跳过openid绑定，原有绑定关系保持不变")
    else:
        # 正常绑定 / 强制改绑
        if has_conflict and request.force_bind:
            # 清空旧用户的 openid（一对一约束）
            old_user.openid = None
            old_user.unionid = None
            old_user.session_key = None
            old_user.updated_at = int(time.time() * 1000)
            session.add(old_user)
            logger.info(f"openid 强制换绑：旧用户 {old_user.id} 的绑定已清除")

        # 对当前用户落库 openid
        user.openid = openid
        user.session_key = new_session_key
        if unionid:
            user.unionid = unionid
        logger.info(f"OpenID 绑定完成: user_id={user.id}, openid={openid[-8:]}...")

    user.updated_at = int(time.time() * 1000)
    session.add(user)
    session.commit()
    session.refresh(user)

    # --- 6. 签发会话 Token（替换JWT）---
    raw_token, token_hash, expires_at = generate_session_token()
    user.token_hash = token_hash
    user.token_expires_at = expires_at
    session.add(user)
    session.commit()
    session.refresh(user)

    return BindLoginResponse(
        token=raw_token,
        user_id=user.id,
        phone_number=user.phone_number,
        openid=openid,
        nickname=user.nickname,
        is_new_user=is_new_user,
        openid_bound=not request.skip_bind,
    )


# --- 接口 B：静默免密登录 ---
class SilentLoginRequest(BaseModel):
    code: str  # wx.login() 返回的临时 code

class SilentLoginResponse(BaseModel):
    token: str
    user_id: int
    phone_number: str
    openid: str
    nickname: Optional[str] = None

@app.post("/api/auth/silent-login", response_model=SilentLoginResponse)
async def auth_silent_login(request: SilentLoginRequest, session: Session = Depends(get_session)):
    """静默免密登录（仅依赖 wx.login code）"""
    code = request.code.strip()

    if not code:
        raise HTTPException(status_code=400, detail="缺少微信登录凭证")

    # --- 1. 向微信换取 openid ---
    wx_data = await wx_code2session(code)
    openid = wx_data["openid"]
    new_session_key = wx_data["session_key"]
    unionid = wx_data.get("unionid")

    if not openid:
        raise HTTPException(status_code=400, detail="获取微信 OpenID 失败")

    # --- 2. 按 openid 直接查 user 表 ---
    user = session.exec(select(User).where(User.openid == openid)).first()

    if not user:
        # 未绑定 → 返回特定错误码，引导前端走账密登录
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNBOUND",
                "message": "该微信尚未绑定账号，请先使用手机号+密码登录完成绑定",
            },
        )

    # --- 3. 更新 session_key ---
    user.session_key = new_session_key
    if unionid:
        user.unionid = unionid
    user.updated_at = int(time.time() * 1000)
    session.add(user)
    session.commit()
    session.refresh(user)

    # --- 4. 签发会话 Token（替换JWT）---
    raw_token, token_hash, expires_at = generate_session_token()
    user.token_hash = token_hash
    user.token_expires_at = expires_at
    session.add(user)
    session.commit()
    session.refresh(user)

    return SilentLoginResponse(
        token=raw_token,
        user_id=user.id,
        phone_number=user.phone_number,
        openid=openid,
        nickname=user.nickname,
    )

# /api/config/* 端点已废弃，设备配置管理请使用 /api/devices/* 系列 API


# --- Device Management APIs ---
from .utils.config_manager import get_user_devices, add_device as add_device_to_db, delete_device
from .utils.config_manager import get_config_from_db, set_config_to_db as set_config_db


class ScaleBindRequest(BaseModel):
    device_id: str  # BLE MAC address, e.g. "XX:XX:XX:XX:XX:XX"
    device_name: str  # BLE device name


class AddDeviceRequest(BaseModel):
    device_type: str
    device_name: Optional[str] = None
    platform: str
    account: str
    password: str

class DeviceResponse(BaseModel):
    device_key: str
    device_type: str
    device_name: Optional[str]
    platform: str
    status: str

class ScaleBindResponse(BaseModel):
    device_key: str
    device_id: str
    device_name: str
    status: str


@app.post("/api/devices/scale/bind", response_model=ScaleBindResponse)
async def bind_scale_device(request: ScaleBindRequest, user_id: str):
    """Bind a BLE scale device to user account — no cloud credentials needed"""
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")

    ble_device_id = request.device_id.strip().lower()
    ble_device_name = request.device_name.strip()

    if not ble_device_id:
        raise HTTPException(status_code=400, detail="设备ID不能为空")

    try:
        # Step 1: 防重复校验 — 查询该用户是否已绑定同一BLE设备
        existing_id = await get_config_from_db(
            key='ble_device_id', user_id=uid, platform='xiaomi'
        )
        if existing_id and existing_id == ble_device_id:
            raise HTTPException(status_code=409, detail="无法重复添加同一蓝牙设备")

        # Step 2: 校验是否已绑定了体脂秤（一个用户只能有一个）
        from .utils.config_manager import get_user_devices
        existing_devices = await get_user_devices(uid, platform='xiaomi')
        if existing_devices:
            raise HTTPException(status_code=409, detail="已绑定体脂秤，请先删除现有设备再重新绑定")

        # Step 3: 存储体脂秤绑定信息 — 使用新存储规则：device_name=蓝牙名，仅存ble_address
        from .utils.config_manager import add_ble_device
        device_key = await add_ble_device(uid, ble_device_id, ble_device_name)

        # Step 4: 清除仪表盘缓存 + 设备缓存
        from .utils.device_cache import device_cache
        await device_cache.invalidate_user(uid)
        cache_prefix = f'user_{uid}'
        for cache_key in [
            f'{cache_prefix}_dashboard_combined_data',
            f'{cache_prefix}_cloudpets_servings',
            f'{cache_prefix}_cloudpets_plans',
            f'{cache_prefix}_petkit_devices',
        ]:
            await cache_manager.delete(cache_key)

        logger.info(f"体脂秤绑定成功: user={uid}, device={ble_device_id}, name={ble_device_name}")

        return ScaleBindResponse(
            device_key=f"xiaomi_{ble_device_name}",
            device_id=ble_device_id,
            device_name=ble_device_name,
            status="active",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"体脂秤绑定失败: {e}")
        raise HTTPException(status_code=500, detail=f"体脂秤绑定失败: {str(e)}")


@app.get("/api/devices/scale/bound")
async def get_bound_scale_device(user_id: str):
    """获取已绑定的体脂秤 BLE 设备信息"""
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")

    try:
        ble_device_id = await get_config_from_db(
            key='ble_device_id', user_id=uid, platform='xiaomi'
        )
        ble_device_name = await get_config_from_db(
            key='ble_device_name', user_id=uid, platform='xiaomi'
        )

        if not ble_device_id:
            return {"bound": False, "device_id": None, "device_name": None}

        return {
            "bound": True,
            "device_id": ble_device_id,
            "device_name": ble_device_name or "小米体脂秤",
        }
    except Exception as e:
        logger.error(f"获取体脂秤绑定信息失败: {e}")
        return {"bound": False, "device_id": None, "device_name": None}


@app.post("/api/devices/add", response_model=DeviceResponse)
async def add_device_api(request: AddDeviceRequest, user_id: str):
    """Add device to user account — 先验证凭据有效，再持久化到DB"""
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    try:
        # Step 1: 验证凭据（platform='xiaomi'=体脂秤，跳过云验证）
        token = ''
        is_scale = request.platform == 'xiaomi'
        if is_scale:
            init_ok = True
        else:
            init_ok = await _init_service_for_user(
                request.platform, uid, request.account, request.password
            )
            if init_ok:
                if request.platform == "petkit" and state.petkit is not None:
                    mem_session = getattr(state.petkit, '_memory_session', None)
                    if mem_session:
                        token = json.dumps(mem_session)
                        logger.info(f"[AddDevice] PetKit token 已提取: user={uid}")
                elif request.platform == "cloudpets":
                    cp = state.cloudpets
                    if cp is not None:
                        mem_token = getattr(cp, '_memory_token', None)
                        if mem_token:
                            token = mem_token
                            logger.info(f"[AddDevice] CloudPets token 已提取: user={uid}")
                if not token:
                    logger.warning(f"[AddDevice] {request.platform} token 为空，user={uid} 的设备配置组将 is_complete=False")
        if not init_ok:
            raise HTTPException(
                status_code=400,
                detail=f"{request.platform} 登录失败：账号或密码错误，请检查后重试",
            )

        # Step 2: 持久化到 DB
        from .utils.config_manager import add_cloud_device, add_ble_device
        if is_scale:
            device_key = await add_ble_device(uid, request.account, request.device_name or 'MIBFS')
        else:
            device_key = await add_cloud_device(
                user_id=uid, platform=request.platform,
                account=request.account, password=request.password,
                token=token,
                device_name=request.device_name,
            )

        # Step 3: 清除缓存（泛化键，不硬编码平台名）
        from .utils.device_cache import device_cache
        await device_cache.invalidate_user(uid)
        cache_prefix = f'user_{uid}'
        await cache_manager.delete(f'{cache_prefix}_dashboard_combined_data')
        await cache_manager.delete(f'{cache_prefix}_petkit_devices')
        await cache_manager.delete(f'{cache_prefix}_cloudpets_servings')
        await cache_manager.delete(f'{cache_prefix}_cloudpets_plans')
        logger.info(f"[AddDevice] 缓存已清除: user={uid}")

        return DeviceResponse(
            device_key=device_key,
            device_type=request.device_type,
            device_name=request.device_name or f"{request.platform}_device",
            platform=request.platform,
            status="active",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加设备失败：{str(e)}")

@app.delete("/api/devices/{device_key}")
async def delete_device_api(device_key: str, user_id: str):
    """Delete device from user account (with confirmation)"""
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    try:
        # 检查是否为共享设备（被分享者无权删除）
        from .models.models import SharedDeviceConfig
        from .models.db import engine
        from sqlmodel import select
        with Session(engine) as _s:
            shared = _s.exec(
                select(SharedDeviceConfig).where(
                    SharedDeviceConfig.to_user_id == uid,
                    SharedDeviceConfig.device_key == device_key,
                )
            ).first()
            if shared:
                raise HTTPException(status_code=403, detail="共享设备不支持删除操作，请联系分享者处理")

        # 使用新存储规则：按 platform 即可唯一定位
        platform = device_key.split('_')[0] if '_' in device_key else device_key

        if platform == 'xiaomi':
            # 体脂秤：软删除 BLE 配置 + 清除家庭成员
            from .utils.config_manager import delete_device_by_platform
            try:
                await delete_device_by_platform(uid, 'xiaomi')
            except Exception:
                pass
            try:
                from sqlmodel import select
                from .models.models import FamilyMember
                from .models.db import engine
                with Session(engine) as session:
                    stmt = select(FamilyMember).where(
                        FamilyMember.user_id == uid,
                        FamilyMember.is_active == True
                    )
                    members = session.exec(stmt).all()
                    for m in members:
                        m.is_active = False
                    session.commit()
            except Exception:
                pass
        else:
            from .utils.config_manager import delete_device_by_platform
            success = await delete_device_by_platform(uid, platform)
            if not success:
                # 兼容旧 device_key 格式
                success = await delete_device(uid, device_key)
                if not success:
                    raise HTTPException(status_code=404, detail="设备不存在")

        # Clear all caches
        from .utils.device_cache import device_cache
        await device_cache.invalidate_user(uid)
        cache_prefix = f'user_{uid}'
        await cache_manager.delete(f'{cache_prefix}_dashboard_combined_data')
        await cache_manager.delete(f'{cache_prefix}_cloudpets_servings')
        await cache_manager.delete(f'{cache_prefix}_cloudpets_plans')
        await cache_manager.delete(f'{cache_prefix}_petkit_devices')
        logger.info(f"Cleared all caches for user {uid} after device deletion")

        return {"status": "success", "message": "设备删除成功", "device_key": device_key}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除设备失败：{str(e)}")

# --- System Config APIs ---
from .utils.config_manager import get_config_from_db, set_config_to_db

class SystemConfigRequest(BaseModel):
    platform: str
    account: str
    password: str

class SystemConfigResponse(BaseModel):
    cloudpets_account: Optional[str] = None
    cloudpets_password: Optional[str] = None
    petkit_account: Optional[str] = None
    petkit_password: Optional[str] = None
@app.get("/api/system/config", response_model=SystemConfigResponse)
async def get_system_config():
    """Get system configuration (passwords masked)"""
    try:
        # Get first user ID for each platform
        cp_user = await _get_first_user_with_platform("cloudpets")
        pk_user = await _get_first_user_with_platform("petkit")
        config = SystemConfigResponse()

        if cp_user:
            config.cloudpets_account = await get_config_from_db("account", user_id=cp_user, platform="cloudpets")
            has_pwd = await get_config_from_db("password", user_id=cp_user, platform="cloudpets")
            config.cloudpets_password = "********" if has_pwd else None

        if pk_user:
            config.petkit_account = await get_config_from_db("account", user_id=pk_user, platform="petkit")
            has_pwd = await get_config_from_db("password", user_id=pk_user, platform="petkit")
            config.petkit_password = "********" if has_pwd else None

        return config
    except Exception as e:
        logger.error(f"Failed to get config: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置失败：{str(e)}")

@app.post("/api/system/config")
async def save_system_config(request: SystemConfigRequest):
    """Save system configuration for a platform"""
    try:
        # Find or create user for this platform
        user_id = await _get_first_user_with_platform(request.platform)
        if not user_id:
            # Create a default user if none exists
            from .models.models import User
            from .models.db import engine
            from sqlmodel import Session
            loop = asyncio.get_running_loop()
            def _create_user():
                with Session(engine) as session:
                    user = User(phone_number="00000000000", nickname="系统用户")
                    session.add(user)
                    session.commit()
                    session.refresh(user)
                    return user.id
            user_id = await loop.run_in_executor(None, _create_user)

        # Save config
        await set_config_to_db("account", user_id, request.account, is_encrypted=True, platform=request.platform)
        await set_config_to_db("password", user_id, request.password, is_encrypted=True, platform=request.platform)

        # Re-initialize service
        await _init_service_for_user(request.platform, user_id, request.account, request.password)

        return {"message": "配置保存成功", "platform": request.platform}
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置失败：{str(e)}")

# --- Family Member APIs ---
class FamilyMemberRequest(BaseModel):
    name: str
    gender: str = ""
    age: int = 0
    height: float = 0
    avatar_color: str = ""
    relationship: str = ""

class FamilyMemberResponse(BaseModel):
    id: int
    user_id: int
    name: str
    gender: str
    age: int
    height: float
    avatar_color: str
    relationship: str
    sort_order: int
    is_active: bool
    created_at: int
    updated_at: int

@app.get("/api/family-members", response_model=List[FamilyMemberResponse])
async def get_family_members(user_id: str, session: Session = Depends(get_session)):
    """Get all family members for a user"""
    try:
        uid = int(user_id)
        stmt = select(FamilyMember).where(
            FamilyMember.user_id == uid,
            FamilyMember.is_active == True
        ).order_by(FamilyMember.sort_order, FamilyMember.created_at)
        members = session.exec(stmt).all()
        return members
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取家庭成员失败：{str(e)}")

@app.post("/api/family-members", response_model=FamilyMemberResponse)
async def add_family_member(request: FamilyMemberRequest, user_id: str, session: Session = Depends(get_session)):
    """Add a new family member"""
    try:
        uid = int(user_id)

        # Get max sort_order
        stmt = select(FamilyMember.sort_order).where(
            FamilyMember.user_id == uid
        ).order_by(FamilyMember.sort_order.desc()).limit(1)
        result = session.exec(stmt).first()
        max_sort = result if result is not None else 0

        member = FamilyMember(
            user_id=uid,
            name=request.name,
            gender=request.gender,
            age=request.age,
            height=request.height,
            avatar_color=request.avatar_color,
            relationship=request.relationship,
            sort_order=max_sort + 1
        )
        session.add(member)
        session.commit()
        session.refresh(member)

        logger.info(f"Added family member {request.name} for user {uid}")
        return member
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"添加家庭成员失败：{str(e)}")

@app.put("/api/family-members/{member_id}", response_model=FamilyMemberResponse)
async def update_family_member(member_id: int, request: FamilyMemberRequest, user_id: str, session: Session = Depends(get_session)):
    """Update a family member"""
    try:
        uid = int(user_id)
        member = session.get(FamilyMember, member_id)

        if not member:
            raise HTTPException(status_code=404, detail="家庭成员不存在")

        if member.user_id != uid:
            raise HTTPException(status_code=403, detail="无权操作此家庭成员")

        member.name = request.name
        member.gender = request.gender
        member.age = request.age
        member.height = request.height
        member.avatar_color = request.avatar_color
        member.relationship = request.relationship
        member.updated_at = int(time.time() * 1000)

        session.add(member)
        session.commit()
        session.refresh(member)

        logger.info(f"Updated family member {member_id} for user {uid}")
        return member
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"更新家庭成员失败：{str(e)}")

@app.delete("/api/family-members/{member_id}")
async def delete_family_member(member_id: int, user_id: str, session: Session = Depends(get_session)):
    """Delete (deactivate) a family member"""
    try:
        uid = int(user_id)
        member = session.get(FamilyMember, member_id)

        if not member:
            raise HTTPException(status_code=404, detail="家庭成员不存在")

        if member.user_id != uid:
            raise HTTPException(status_code=403, detail="无权操作此家庭成员")

        # Soft delete: set is_active to False
        member.is_active = False
        member.updated_at = int(time.time() * 1000)

        session.add(member)
        session.commit()

        logger.info(f"Deleted family member {member_id} for user {uid}")
        return {"status": "success", "message": "删除成功"}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"删除家庭成员失败：{str(e)}")

@app.get("/api/family-members/{member_id}/history")
async def get_member_history(member_id: int, user_id: str, limit: int = 30, session: Session = Depends(get_session)):
    """Get weight history for a family member"""
    try:
        uid = int(user_id)

        # Verify member belongs to user
        member = session.get(FamilyMember, member_id)
        if not member or member.user_id != uid:
            raise HTTPException(status_code=403, detail="无权访问此成员数据")

        # Query weight records
        stmt = select(WeightRecord).where(
            WeightRecord.member_id == member_id
        ).order_by(WeightRecord.timestamp.desc()).limit(limit)
        records = session.exec(stmt).all()

        history = []
        for record in records:
            history.append({
                "id": record.id,
                "weight": record.weight,
                "bmi": record.bmi,
                "body_fat": record.body_fat,
                "water": record.water,
                "muscle_mass": record.muscle,
                "protein": record.protein if hasattr(record, 'protein') else None,
                "bmr": record.bmr,
                "bone_mass": record.bone_mass,
                "visceral_fat": record.visceral_fat,
                "timestamp": record.timestamp,
                "created_at": record.created_at
            })

        return history
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史记录失败：{str(e)}")

# --- Scale Measurement APIs (小程序专用) ---
class ScaleMeasurementRequest(BaseModel):
    member_id: int
    weight: float
    impedance: int = 0
    bmi: Optional[float] = None
    body_fat: Optional[float] = None
    water: Optional[float] = None
    muscle_mass: Optional[float] = None
    protein: Optional[float] = None
    bmr: Optional[float] = None
    visceral_fat: Optional[float] = None
    bone_mass: Optional[float] = None  # 添加骨量字段

@app.post("/api/scale/measurements")
async def create_scale_measurement(request: ScaleMeasurementRequest, session: Session = Depends(get_session)):
    """Create a new scale measurement record with time-slot deduplication"""
    try:
        # Verify member exists and is active
        member = session.get(FamilyMember, request.member_id)
        if not member or not member.is_active:
            raise HTTPException(status_code=404, detail="家庭成员不存在或已禁用")

        # 计算当前时段（早中晚夜宵）
        now = datetime.now()
        hour = now.hour
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        # 时段定义
        if 5 <= hour < 10:
            meal_period = 'breakfast'  # 早餐前
        elif 10 <= hour < 14:
            meal_period = 'lunch'      # 午餐前
        elif 14 <= hour < 18:
            meal_period = 'dinner'     # 晚餐前
        else:
            meal_period = 'supper'     # 夜宵/睡前

        # 查询该成员今日该时段是否已有记录
        period_start_ts = int(today_start.timestamp() * 1000)
        period_end_ts = int(today_end.timestamp() * 1000)

        existing_stmt = select(WeightRecord).where(
            WeightRecord.member_id == request.member_id,
            WeightRecord.timestamp >= period_start_ts,
            WeightRecord.timestamp <= period_end_ts
        ).order_by(WeightRecord.timestamp.desc())

        existing_records = session.exec(existing_stmt).all()

        # 查找同一时段的记录
        same_period_record = None
        for record in existing_records:
            record_time = datetime.fromtimestamp(record.timestamp / 1000)
            record_hour = record_time.hour

            if 5 <= record_hour < 10 and meal_period == 'breakfast':
                same_period_record = record
                break
            elif 10 <= record_hour < 14 and meal_period == 'lunch':
                same_period_record = record
                break
            elif 14 <= record_hour < 18 and meal_period == 'dinner':
                same_period_record = record
                break
            elif (record_hour >= 18 or record_hour < 5) and meal_period == 'supper':
                same_period_record = record
                break

        if same_period_record:
            # 覆盖更新同一时段的记录
            same_period_record.weight = request.weight
            same_period_record.impedance = request.impedance
            same_period_record.bmi = request.bmi
            same_period_record.body_fat = request.body_fat
            same_period_record.water = request.water
            same_period_record.muscle = request.muscle_mass
            same_period_record.protein = request.protein
            same_period_record.bmr = request.bmr
            same_period_record.bone_mass = request.bone_mass  # 添加骨量更新
            same_period_record.visceral_fat = request.visceral_fat
            same_period_record.timestamp = int(time.time() * 1000)

            session.add(same_period_record)
            session.commit()
            session.refresh(same_period_record)

            logger.info(f"Updated {meal_period} measurement for member {request.member_id}: {request.weight}kg")

            record = same_period_record
            message = "更新成功"
        else:
            # 创建新记录
            record = WeightRecord(
                user_id=member.user_id,
                member_id=request.member_id,
                weight=request.weight,
                impedance=request.impedance,
                bmi=request.bmi,
                body_fat=request.body_fat,
                water=request.water,
                muscle=request.muscle_mass,
                protein=request.protein,
                bmr=request.bmr,
                bone_mass=request.bone_mass,  # 使用前端传递的骨量值
                visceral_fat=request.visceral_fat,
                timestamp=int(time.time() * 1000),
                created_at=int(time.time() * 1000)
            )

            session.add(record)
            session.commit()
            session.refresh(record)

            logger.info(f"Created {meal_period} measurement for member {request.member_id}: {request.weight}kg")
            message = "保存成功"



        return {
            "code": 200,
            "message": message,
            "data": {
                "id": record.id,
                "weight": record.weight,
                "timestamp": record.timestamp,
                "meal_period": meal_period
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to create scale measurement: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败：{str(e)}")

@app.get("/api/scale/members")
async def get_scale_members(user_id: Optional[int] = None, session: Session = Depends(get_session)):
    """Get all active family members for scale page"""
    try:
        if not user_id:
            # Try to get from first configured user
            user_id = await _get_first_user_with_platform("cloudpets") or await _get_first_user_with_platform("petkit")
            if not user_id:
                return {"code": 200, "data": []}

        stmt = select(FamilyMember).where(
            FamilyMember.user_id == user_id,
            FamilyMember.is_active == True
        ).order_by(FamilyMember.sort_order, FamilyMember.created_at)
        members = session.exec(stmt).all()

        result = []
        for member in members:
            # Get latest weight for this member
            weight_stmt = select(WeightRecord).where(
                WeightRecord.member_id == member.id
            ).order_by(WeightRecord.timestamp.desc()).limit(1)
            latest_record = session.exec(weight_stmt).first()

            # Get weight history count
            history_stmt = select(WeightRecord).where(
                WeightRecord.member_id == member.id
            )
            history_count = len(session.exec(history_stmt).all())

            result.append({
                "id": member.id,
                "name": member.name,
                "gender": member.gender,
                "age": member.age,
                "height": member.height,
                "avatar_color": member.avatar_color,
                "relationship": member.relationship,
                "last_weight": latest_record.weight if latest_record else None,
                "sort_order": member.sort_order
            })

        return {"code": 200, "data": result}
    except Exception as e:
        logger.error(f"Failed to get scale members: {e}")
        raise HTTPException(status_code=500, detail=f"获取成员列表失败：{str(e)}")

@app.put("/api/scale/members/{member_id}")
async def update_scale_member(member_id: int, request: FamilyMemberRequest, session: Session = Depends(get_session)):
    """Update a family member (alias for PUT /api/family-members/{member_id})"""
    try:
        member = session.get(FamilyMember, member_id)
        if not member:
            raise HTTPException(status_code=404, detail="家庭成员不存在")

        member.name = request.name
        member.gender = request.gender
        member.age = request.age
        member.height = request.height
        member.avatar_color = request.avatar_color
        member.relationship = request.relationship
        member.updated_at = int(time.time() * 1000)

        session.add(member)
        session.commit()
        session.refresh(member)

        return {"code": 200, "message": "修改成功", "data": {"id": member.id}}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to update member: {e}")
        raise HTTPException(status_code=500, detail=f"修改失败：{str(e)}")


# ---------------------------------------------------------------------------
# 手机号注册状态查询 API
# ---------------------------------------------------------------------------

@app.get("/api/auth/check-phone")
async def check_phone_exists(phone: str, session: Session = Depends(get_session)):
    """查询手机号是否已注册"""
    if not phone or len(phone) != 11:
        raise HTTPException(status_code=400, detail="请输入正确的11位手机号")
    user = session.exec(select(User).where(User.phone_number == phone)).first()
    return {"exists": user is not None}


# ---------------------------------------------------------------------------
# 账号管理与隐私合规 API
# ---------------------------------------------------------------------------

class ChangePasswordRequest(BaseModel):
    password: str

@app.post("/api/auth/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """修改当前登录用户的密码"""
    try:
        new_password = request.password.strip()
        if not new_password or len(new_password) < 4:
            raise HTTPException(status_code=400, detail="密码长度不能少于4位")

        current_user.password_hash = hash_password(new_password)
        current_user.updated_at = int(time.time() * 1000)
        session.add(current_user)
        session.commit()

        logger.info(f"密码修改成功: user_id={current_user.id}")
        return {"status": "success", "message": "密码修改成功"}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"修改密码失败：{str(e)}")


class DeleteAccountRequest(BaseModel):
    pass

@app.post("/api/auth/delete-account")
async def delete_account(
    request: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """注销当前登录用户的账号：删除用户及所有相关数据"""
    try:
        user_id = current_user.id
        user = current_user

        logger.warning(f"开始注销账号: user_id={user_id}, phone={user.phone_number}")

        # 1. 删除所有体重记录
        weight_stmt = select(WeightRecord).where(WeightRecord.user_id == user_id)
        weight_records = session.exec(weight_stmt).all()
        for record in weight_records:
            session.delete(record)
        logger.info(f"已删除 {len(weight_records)} 条体重记录")

        # 2. 删除所有家庭成员
        member_stmt = select(FamilyMember).where(FamilyMember.user_id == user_id)
        members = session.exec(member_stmt).all()
        for member in members:
            session.delete(member)
        logger.info(f"已删除 {len(members)} 个家庭成员")

        # 3. 删除所有设备配置
        config_stmt = select(SystemConfig).where(SystemConfig.user_id == user_id)
        configs = session.exec(config_stmt).all()
        for cfg in configs:
            session.delete(cfg)
        logger.info(f"已删除 {len(configs)} 条配置")

        # 4. 删除分享记录（作为分享者和被分享者）
        from .models.models import DeviceShare, SharedDeviceConfig
        share_stmt = select(DeviceShare).where(
            (DeviceShare.from_user_id == user_id) | (DeviceShare.to_user_id == user_id)
        )
        shares = session.exec(share_stmt).all()
        for share in shares:
            # 删除分享的配置映射
            sub_stmt = select(SharedDeviceConfig).where(SharedDeviceConfig.share_id == share.id)
            sub_configs = session.exec(sub_stmt).all()
            for sc in sub_configs:
                session.delete(sc)
            session.delete(share)
        logger.info(f"已删除 {len(shares)} 条分享记录")

        # 5. 删除用户本身
        session.delete(user)
        session.commit()

        logger.warning(f"账号已完全注销: user_id={user_id}")
        return {
            "status": "success",
            "message": "账号已成功注销，所有数据已永久删除",
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"注销失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"注销失败：{str(e)}")

