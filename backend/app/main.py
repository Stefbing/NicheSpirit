"""Smart Home Controller API - Main Application"""
import os, uvicorn, asyncio, time, logging, hashlib, hmac, json
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional, List

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlmodel import Session, select

from .services.petkit_service import PetKitService
from .services.cloudpets_service import cloudpets_service, FeedingPlan as CloudPetsPlan
from .services.xiaomi_service import xiaomi_service
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
        self.cloudpets = None
        self.data_refresh_task = None
        self.xiaomi_initialized: bool = False

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
    """Unified service initialization helper (CloudPets/PetKit only)"""
    try:
        logger.info(f"Initializing {platform} service for user {user_id}...")
        if platform == "petkit":
            state.petkit = PetKitService(account, password, user_id=user_id)
            success = await state.petkit.initialize()
            logger.info(f"{'✓' if success else '⚠'} PetKit init {'success' if success else 'failed'}")
            return success
        elif platform == "cloudpets":
            from .utils.config_manager import set_config_to_db
            await set_config_to_db("account", user_id, account, is_encrypted=True, platform="cloudpets")
            await set_config_to_db("password", user_id, password, is_encrypted=True, platform="cloudpets")
            import backend.app.services.cloudpets_service as cp_module
            state.cloudpets = cp_module.CloudPetsService(user_id=user_id)
            success = await state.cloudpets.initialize()
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
    
    # Add scheduled tasks
    async def refresh_dashboard_cache():
        """后台定期刷新所有用户的仪表板缓存（无感刷新）"""
        try:
            from .utils.config_manager import get_configs_batch
            from sqlmodel import Session, select
            from .models.models import SystemConfig
            from .models.db import engine  # 【修复】导入 engine
            
            logger.info("Starting background dashboard cache refresh...")
            start_time = time.time()
            
            # 获取所有有配置的用户ID
            loop = asyncio.get_running_loop()
            
            def _get_user_ids():
                with Session(engine) as session:
                    stmt = select(SystemConfig.user_id).where(
                        SystemConfig.platform.in_(["petkit", "cloudpets", "xiaomi"]),  # 【修复】添加 xiaomi
                        SystemConfig.key == "account",
                        SystemConfig.is_active == True
                    ).distinct()
                    return list(session.exec(stmt).all())
            
            user_ids = await loop.run_in_executor(None, _get_user_ids)
            
            if not user_ids:
                logger.debug("No users with platform config found")
                return
            
            logger.info(f"Refreshing cache for {len(user_ids)} users: {user_ids}")
            
            # 并行刷新所有用户的缓存
            async def refresh_single_user(uid):
                try:
                    # 清除旧缓存
                    cache_prefix = f'user_{uid}'
                    await cache_manager.delete(f'{cache_prefix}_dashboard_combined_data')
                    
                    # 触发重新生成（调用dashboard接口逻辑）
                    # 注意：这里不直接调用接口函数，而是执行相同的逻辑
                    config_queries = [
                        ("account", uid, "petkit"),
                        ("password", uid, "petkit"),
                        ("account", uid, "cloudpets"),
                        ("password", uid, "cloudpets"),
                        ("account", uid, "xiaomi"),  # 【修复】添加 xiaomi
                        ("password", uid, "xiaomi"),  # 【修复】添加 xiaomi
                    ]
                    configs = await get_configs_batch(config_queries)
                    
                    petkit_username = configs.get(f"account_{uid}_petkit")
                    petkit_password = configs.get(f"password_{uid}_petkit")
                    cloudpets_account = configs.get(f"account_{uid}_cloudpets")
                    cloudpets_password = configs.get(f"password_{uid}_cloudpets")
                    xiaomi_account = configs.get(f"account_{uid}_xiaomi")
                    xiaomi_password = configs.get(f"password_{uid}_xiaomi")
                    
                    # 只刷新有配置的平台
                    has_petkit = bool(petkit_username and petkit_password)
                    has_cloudpets = bool(cloudpets_account and cloudpets_password)
                    has_xiaomi = bool(xiaomi_account and xiaomi_password)  # 【修复】添加 xiaomi 检查
                    
                    if not has_petkit and not has_cloudpets and not has_xiaomi:  # 【修复】添加 xiaomi 判断
                        return
                    
                    # 获取PetKit设备
                    petkit_devices = []
                    servings = {}
                    plans = []
                    if has_petkit:
                        pk_service, pk_is_temp = await _get_petkit_for_user(uid)
                        if pk_service:
                            try:
                                petkit_devices = await pk_service.get_devices()
                            finally:
                                await _release_service(pk_service, pk_is_temp)
                        await cache_manager.set(f'{cache_prefix}_petkit_devices', petkit_devices, ttl=300)
                    
                    # 获取CloudPets数据
                    if has_cloudpets:
                        cp_service, cp_is_temp = await _get_cloudpets_for_user(uid)
                        if cp_service:
                            try:
                                servings = await cp_service.get_servings_today() or {}
                                plans = await cp_service.get_feeding_plans() or []
                            finally:
                                await _release_service(cp_service, cp_is_temp)
                        
                        await cache_manager.set(f'{cache_prefix}_cloudpets_servings', servings, ttl=120)
                        await cache_manager.set(f'{cache_prefix}_cloudpets_plans', plans, ttl=300)
                    
                    # 获取PetKit设备统计（复用已获取的服务实例）
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
                    
                    # 【优化】获取体脂秤统计数据（使用 COUNT + LIMIT 1，避免加载全部记录）
                    scale_stats = {'today_count': 0, 'latest_body_fat': None}
                    if has_xiaomi:
                        try:
                            from datetime import datetime, timedelta
                            from .models.models import WeightRecord
                            from sqlmodel import select
                            from .models.db import engine
                            from sqlalchemy import func
                            
                            loop = asyncio.get_running_loop()
                            
                            def _query_scale_stats():
                                with Session(engine) as db_session:
                                    today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
                                    today_end = int(datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999).timestamp() * 1000)
                                    
                                    # 优化：使用 COUNT 统计而非加载全部记录
                                    count_stmt = select(func.count()).select_from(WeightRecord).where(
                                        WeightRecord.user_id == uid,
                                        WeightRecord.timestamp >= today_start,
                                        WeightRecord.timestamp <= today_end
                                    )
                                    today_count = db_session.exec(count_stmt).one()
                                    
                                    # 优化：只查询最新 1 条记录的体脂率
                                    latest_stmt = select(WeightRecord.body_fat).where(
                                        WeightRecord.user_id == uid,
                                        WeightRecord.timestamp >= today_start,
                                        WeightRecord.timestamp <= today_end,
                                        WeightRecord.body_fat.isnot(None)
                                    ).order_by(WeightRecord.timestamp.desc()).limit(1)
                                    latest_body_fat_raw = db_session.exec(latest_stmt).first()
                                    latest_body_fat = round(latest_body_fat_raw, 1) if latest_body_fat_raw else None
                                    
                                    return {
                                        'today_count': today_count,
                                        'latest_body_fat': latest_body_fat
                                    }
                            
                            scale_stats = await loop.run_in_executor(None, _query_scale_stats)
                            logger.info(f'[Background Refresh] User {uid} - 今日测量: {scale_stats["today_count"]}, 最新体脂: {scale_stats["latest_body_fat"]}')
                        except Exception as e:
                            logger.error(f'[Background Refresh] 获取体脂秤统计失败: {e}')
                    
                    # 构建组合缓存（使用显式变量，避免 locals() 脆弱引用）
                    dashboard_data = {
                        'petkit_devices': petkit_devices,
                        'litterbox_stats': {},
                        'cloudpets_servings': servings,
                        'cloudpets_plans': plans,
                        'xiaomi_config': bool(xiaomi_account and xiaomi_password),
                        'scale_stats': scale_stats
                    }
                    await cache_manager.set(f'{cache_prefix}_dashboard_combined_data', dashboard_data, ttl=120)
                    
                    logger.info(f"✓ Cache refreshed for user {uid}")
                except Exception as e:
                    logger.error(f"Failed to refresh cache for user {uid}: {e}")
            
            # 并行刷新所有用户
            await asyncio.gather(*[refresh_single_user(uid) for uid in user_ids], return_exceptions=True)
            
            elapsed = time.time() - start_time
            logger.info(f"Dashboard cache refresh completed in {elapsed:.2f}s")
            
        except Exception as e:
            logger.error(f"Dashboard cache refresh failed: {e}")
    
    # 每90秒刷新一次缓存（比缓存过期时间60s长，确保在过期前刷新）
    await scheduler.add_task('dashboard_cache_refresh', refresh_dashboard_cache, interval=90, immediate=False)
    await scheduler.start()
    
    # 启动缓存后台定期清理（惰性过期 + LRU 淘汰）
    cache_manager.start_background_cleanup()
    
    logger.info(f"=== App initialized in {time.time() - start_time:.2f}s ===")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await scheduler.stop()
    await cache_manager.stop_background_cleanup()
    if state.petkit:
        await state.petkit.close()
    if state.cloudpets:
        await state.cloudpets.close()

# --- App Config ---
app = FastAPI(title="Smart Home Controller", version="0.3.0", lifespan=lifespan)
app.include_router(share_router)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(os.path.dirname(BASE_DIR), "static")

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
else:
    logger.warning(f"Static directory not found: {STATIC_DIR}")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_petkit():
    """Get logged-in PetKit instance"""
    if not state.petkit:
        raise HTTPException(status_code=503, detail="PetKit service not initialized")
    return state.petkit


# --- 公共服务获取工具（消除重复代码） ---
async def _get_user_credentials(user_id: int, platform: str):
    """获取用户指定平台的账号密码，返回 (account, password) 或 (None, None)"""
    from .utils.config_manager import get_configs_batch
    configs = await get_configs_batch([
        ("account", user_id, platform),
        ("password", user_id, platform),
    ])
    account = configs.get(f"account_{user_id}_{platform}")
    password = configs.get(f"password_{user_id}_{platform}")
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
    返回 (service, is_temp) 元组
    """
    if state.cloudpets and getattr(state.cloudpets, 'user_id', None) == user_id:
        return state.cloudpets, False
    import backend.app.services.cloudpets_service as cp_module
    temp = cp_module.CloudPetsService(user_id=user_id)
    await temp.initialize()
    return temp, True


async def _release_service(service, is_temp):
    """安全释放临时服务"""
    if is_temp and service and hasattr(service, 'close'):
        try:
            await service.close()
        except Exception:
            pass

# --- Static Routes ---
@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))

@app.get("/litterbox")
async def litterbox_page():
    return FileResponse(os.path.join(STATIC_DIR, 'litterbox.html'))

@app.get("/feeder")
async def feeder_page():
    return FileResponse(os.path.join(STATIC_DIR, 'feeder.html'))

@app.get("/feeder/plans")
async def feeder_plans_page():
    return FileResponse(os.path.join(STATIC_DIR, 'feeder_plans.html'))

@app.get("/scale")
async def scale_page():
    return FileResponse(os.path.join(STATIC_DIR, 'scale.html'))

@app.get("/config")
async def config_page():
    return FileResponse(os.path.join(STATIC_DIR, 'config.html'))

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
        
        # 批量获取所有配置（减少数据库查询次数）
        config_queries = [
            ("account", user_id, "petkit"),
            ("password", user_id, "petkit"),
            ("account", user_id, "cloudpets"),
            ("password", user_id, "cloudpets"),
            ("account", user_id, "xiaomi"),
            ("password", user_id, "xiaomi"),
        ]
        configs = await get_configs_batch(config_queries)
        
        petkit_username = configs.get(f"account_{user_id}_petkit")
        petkit_password = configs.get(f"password_{user_id}_petkit")
        cloudpets_account = configs.get(f"account_{user_id}_cloudpets")
        cloudpets_password = configs.get(f"password_{user_id}_cloudpets")
        xiaomi_account = configs.get(f"account_{user_id}_xiaomi")
        xiaomi_password = configs.get(f"password_{user_id}_xiaomi")
        
        dashboard_data = {}
        
        # 并行获取PetKit设备和CloudPets数据
        async def fetch_petkit_devices():
            """获取PetKit设备列表"""
            petkit_devices = await cache_manager.get(f'{cache_prefix}_petkit_devices')
            if petkit_devices:
                return petkit_devices
            
            if not petkit_username or not petkit_password:
                return []
            
            # Try to initialize service for this user
            if state.petkit and getattr(state.petkit, 'user_id', None) == user_id:
                devices = await state.petkit.get_devices()
            else:
                temp_service = PetKitService(petkit_username, petkit_password, user_id=user_id)
                await temp_service.initialize()
                devices = await temp_service.get_devices()
                await temp_service.close()
            
            await cache_manager.set(f'{cache_prefix}_petkit_devices', devices, ttl=300)
            return devices
        
        async def fetch_cloudpets_data():
            """获取CloudPets数据（servings + plans）"""
            result = {"servings": {}, "plans": []}
            
            if not cloudpets_account or not cloudpets_password:
                return result
            
            # Initialize or use existing service
            if state.cloudpets and getattr(state.cloudpets, 'user_id', None) == user_id:
                servings = await state.cloudpets.get_servings_today()
                plans = await state.cloudpets.get_feeding_plans()
            else:
                import backend.app.services.cloudpets_service as cp_module
                temp_service = cp_module.CloudPetsService(user_id=user_id)
                await temp_service.initialize()
                servings = await temp_service.get_servings_today()
                plans = await temp_service.get_feeding_plans()
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
        
        # Xiaomi scale config check
        dashboard_data['xiaomi_config'] = bool(xiaomi_account and xiaomi_password)
        
        # 【新增】获取体脂秤统计数据
        if xiaomi_account and xiaomi_password:
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
                    # 按时间倒序，取第一条
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
        else:
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
        
        from .utils.config_manager import get_config_from_db
        username = await get_config_from_db("account", user_id=user_id, platform="petkit")
        password = await get_config_from_db("password", user_id=user_id, platform="petkit")
        
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
        
        from .utils.config_manager import get_config_from_db
        username = await get_config_from_db("account", user_id=user_id, platform="petkit")
        password = await get_config_from_db("password", user_id=user_id, platform="petkit")
        
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
        from .utils.config_manager import get_config_from_db
        username = await get_config_from_db("account", user_id=user_id, platform="petkit")
        password = await get_config_from_db("password", user_id=user_id, platform="petkit")
        
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

@app.post("/api/petwant/feed")
async def petwant_feed():
    return {"status": "error", "message": "Use /api/cloudpets/feed instead."}

# --- Scale & User APIs ---
@app.get("/api/users", response_model=List[User])
def get_users(session: Session = Depends(get_session)):
    return session.exec(select(User)).all()

@app.post("/api/users", response_model=User)
def create_user(user: User, session: Session = Depends(get_session)):
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

# --- Xiaomi Cloud APIs ---
@app.get("/api/xiaomi/status")
async def xiaomi_status(user_id: Optional[int] = None):
    """Get Xiaomi service status for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("xiaomi")
            if not user_id:
                return {"initialized": False}
        
        from .utils.config_manager import get_config_from_db
        username = await get_config_from_db("account", user_id=user_id, platform="xiaomi")
        password = await get_config_from_db("password", user_id=user_id, platform="xiaomi")
        
        has_credentials = bool(username and password)
        return {
            "initialized": has_credentials,
            "user_id": user_id if has_credentials else None,
            "has_token": has_credentials
        }
    except Exception as e:
        logger.error(f"Failed to get Xiaomi status: {e}")
        return {"initialized": False}

@app.post("/api/xiaomi/login")
async def xiaomi_login(user_id: Optional[int] = None):
    """Login to Xiaomi Cloud for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("xiaomi")
            if not user_id:
                raise HTTPException(status_code=503, detail="Xiaomi service not configured")
        
        from .utils.config_manager import get_config_from_db
        username = await get_config_from_db("account", user_id=user_id, platform="xiaomi")
        password = await get_config_from_db("password", user_id=user_id, platform="xiaomi")
        
        if not username or not password:
            raise HTTPException(status_code=503, detail="Xiaomi credentials missing")
        
        # Create temp service for this user
        import backend.app.services.xiaomi_service as xm_module
        temp_service = xm_module.XiaomiCloudService()
        temp_service.username = username
        temp_service.password = password
        
        success = await temp_service.login()
        if success:
            return {"status": "success", "message": "Login successful"}
        raise HTTPException(status_code=500, detail="Login failed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Login error: {str(e)}")

@app.post("/api/xiaomi/push-weight")
async def push_weight_to_xiaomi(weight: float, body_fat: Optional[float] = None, bmi: Optional[float] = None,
                                 muscle: Optional[float] = None, water: Optional[float] = None,
                                 visceral_fat: Optional[float] = None, bone_mass: Optional[float] = None,
                                 bmr: Optional[float] = None, impedance: Optional[int] = None, user_id: Optional[int] = None):
    """Manually push weight data to Xiaomi Cloud for specific user"""
    try:
        if not user_id:
            user_id = await _get_first_user_with_platform("xiaomi")
            if not user_id:
                raise HTTPException(status_code=503, detail="Xiaomi service not configured")
        
        from .utils.config_manager import get_config_from_db
        username = await get_config_from_db("account", user_id=user_id, platform="xiaomi")
        password = await get_config_from_db("password", user_id=user_id, platform="xiaomi")
        
        if not username or not password:
            raise HTTPException(status_code=503, detail="Xiaomi credentials missing")
        
        # Create temp service for this user
        import backend.app.services.xiaomi_service as xm_module
        temp_service = xm_module.XiaomiCloudService()
        temp_service.username = username
        temp_service.password = password
        
        # Initialize (load token or login)
        initialized = await temp_service.initialize()
        if not initialized:
            raise HTTPException(status_code=503, detail="Xiaomi login failed")
        
        user_data = {"weight": weight, "impedance": impedance or 0, "user_id": user_id}
        if body_fat is not None:
            user_data.update({"body_fat": body_fat, "bmi": bmi, "muscle": muscle, "water": water,
                              "visceral_fat": visceral_fat, "bone_mass": bone_mass, "bmr": bmr})
        
        success = await temp_service.push_weight_data(user_data)
        if success:
            return {"status": "success", "message": "Data pushed to Xiaomi"}
        raise HTTPException(status_code=500, detail="Failed to push data")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Push error: {str(e)}")

@app.get("/api/scale/history/{user_id}")
def get_weight_history(user_id: int, session: Session = Depends(get_session)):
    stmt = select(WeightRecord).where(WeightRecord.user_id == user_id).order_by(WeightRecord.timestamp.desc()).limit(30)
    return session.exec(stmt).all()

def calculate_body_metrics(weight: float, impedance: int, user: User):
    """Simplified body metrics calculation (Xiaomi Scale 2)"""
    height = user.height / 100.0
    bmi = weight / (height * height)
    is_male = user.gender == "male"
    age = user.age
    body_fat = (0.8 * bmi + 0.1 * age - 5.4) if is_male else (0.8 * bmi + 0.1 * age + 4.1)
    if impedance > 0:
        body_fat += (impedance - 500) / 100.0
    body_fat = max(5.0, min(body_fat, 50.0))
    muscle = weight * (1 - body_fat / 100.0) * 0.75
    water = (100 - body_fat) * 0.7
    
    # 计算蛋白质率（占去脂体重的约20-22%）
    lean_mass = weight * (1 - body_fat / 100.0)  # 去脂体重
    protein = (lean_mass * 0.205 / weight) * 100  # 占总体重百分比
    
    # 计算内脏脂肪等级（基于BMI、年龄、性别）
    if is_male:
        visceral_fat = (bmi - 22) * 0.8 + (age - 30) * 0.15
    else:
        visceral_fat = (bmi - 20) * 0.8 + (age - 30) * 0.15
    visceral_fat = max(1.0, min(visceral_fat, 30.0))
    
    bone_mass = weight * 0.04
    bmr = weight * 24.0 if is_male else weight * 22.0
    return {"bmi": round(bmi, 1), "body_fat": round(body_fat, 1), "muscle": round(muscle, 1),
            "water": round(water, 1), "protein": round(protein, 1), "visceral_fat": round(visceral_fat, 1),
            "bone_mass": round(bone_mass, 1), "bmr": round(bmr, 0)}

@app.post("/api/scale/record")
async def record_weight(record: WeightRecord, session: Session = Depends(get_session)):
    if record.impedance and not record.body_fat:
        user = session.get(User, record.user_id)
        if user:
            metrics = calculate_body_metrics(record.weight, record.impedance, user)
            record.bmi = metrics["bmi"]
            record.body_fat = metrics["body_fat"]
            record.muscle = metrics["muscle"]
            record.water = metrics["water"]
            record.protein = metrics["protein"]
            record.visceral_fat = metrics["visceral_fat"]
            record.bone_mass = metrics["bone_mass"]
            record.bmr = metrics["bmr"]
    
    # If member_id is not provided, try to find or create default member
    if not record.member_id:
        stmt = select(FamilyMember).where(
            FamilyMember.user_id == record.user_id,
            FamilyMember.is_active == True
        ).order_by(FamilyMember.sort_order).limit(1)
        default_member = session.exec(stmt).first()
        
        if not default_member:
            # Create default member (current user)
            user = session.get(User, record.user_id)
            if user:
                default_member = FamilyMember(
                    user_id=record.user_id,
                    name=user.nickname or f"用户{user.phone_number[-4:]}",
                    gender=user.gender,
                    age=user.age,
                    height=float(user.height),
                    relationship="self",
                    sort_order=0
                )
                session.add(default_member)
                session.commit()
                session.refresh(default_member)
        
        if default_member:
            record.member_id = default_member.id
    
    session.add(record)
    session.commit()
    session.refresh(record)
    result = {"status": "success", "id": record.id}
    
    # Check if user has Xiaomi configured and push data asynchronously
    try:
        from .utils.config_manager import get_config_from_db
        xm_username = await get_config_from_db("account", user_id=record.user_id, platform="xiaomi")
        xm_password = await get_config_from_db("password", user_id=record.user_id, platform="xiaomi")
        if xm_username and xm_password:
            _track_task(_safe_create_push_task(record, user if record.impedance else None))
    except Exception as e:
        logger.error(f"Failed to check Xiaomi config: {e}")
    
    return result

# --- Auth APIs ---
class UserLoginRequest(BaseModel):
    phone_number: str
    nickname: Optional[str] = None
    gender: str = "male"
    age: int = 25
    height: int = 175

class UserLoginResponse(BaseModel):
    user_id: str
    phone_number: str
    nickname: Optional[str] = None
    has_configured: bool

@app.post("/api/auth/login")
async def user_login(request: UserLoginRequest, session: Session = Depends(get_session)):
    """Mini-program phone login/register with auto service init"""
    try:
        if not request.phone_number or len(request.phone_number) != 11 or not request.phone_number.isdigit():
            raise HTTPException(status_code=400, detail="请输入正确的11位手机号")
        
        user = session.exec(select(User).where(User.phone_number == request.phone_number)).first()
        is_new_user = False
        if not user:
            user = User(phone_number=request.phone_number, nickname=request.nickname or f"用户{request.phone_number[-4:]}",
                        gender=request.gender, age=request.age, height=request.height)
            session.add(user)
            session.commit()
            session.refresh(user)
            logger.info(f"New user registered: {request.phone_number}, ID: {user.id}")
            is_new_user = True
        else:
            if request.nickname: user.nickname = request.nickname
            if request.gender != user.gender: user.gender = request.gender
            if request.age != user.age: user.age = request.age
            if request.height != user.height: user.height = request.height
            session.add(user)
            session.commit()
        
        from .utils.config_manager import get_user_devices
        user_devices = await get_user_devices(user.id)
        has_devices = len(user_devices) > 0
        
        if has_devices:
            logger.info(f"User {request.phone_number} has {len(user_devices)} devices, initializing services...")
            try:
                platforms = {}
                for device in user_devices:
                    platform = device['platform']
                    if platform not in platforms:
                        platforms[platform] = []
                    platforms[platform].append(device)
                for platform, devices in platforms.items():
                    if not devices: continue
                    first_device = devices[0]
                    credentials = first_device.get('credentials', {})
                    account = credentials.get('account')
                    password = credentials.get('password')
                    if not account or not password:
                        logger.warning(f"⚠ {platform} missing credentials")
                        continue
                    await _init_service_for_user(platform, user.id, account, password)
                logger.info("Auto-init completed")
            except Exception as e:
                logger.error(f"Auto-init failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        return UserLoginResponse(user_id=str(user.id), phone_number=user.phone_number,
                                 nickname=user.nickname, has_configured=has_devices)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"登录失败: {str(e)}")

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

@app.post("/api/auth/reinit-services")
async def reinit_services():
    """Reinitialize services after user configures accounts (deprecated - use per-user init)"""
    try:
        return {"status": "success", "message": "服务已按需初始化，无需全局重启"}
    except Exception as e:
        logger.error(f"Failed to reinitialize services: {e}")
        raise HTTPException(status_code=500, detail=f"重新初始化失败: {str(e)}")

# ============================================================================
# WeChat 小程序登录认证（OpenID 绑定 + 静默免密直登）
# ============================================================================

# --- 环境变量配置 ---
JWT_SECRET = os.getenv("JWT_SECRET", "niche-spirit-jwt-secret-2024")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "720"))  # 默认 30 天

WECHAT_APPID = os.getenv("WECHAT_APPID", "")
WECHAT_SECRET = os.getenv("WECHAT_SECRET", "")


def create_jwt_token(user_id: int, phone_number: str, openid: str = "") -> str:
    """生成 JWT Token"""
    import jwt as pyjwt
    payload = {
        "user_id": user_id,
        "phone_number": phone_number,
        "openid": openid,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed: str) -> bool:
    """验证密码"""
    import bcrypt
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed.encode("utf-8"))


async def wx_code2session(code: str) -> dict:
    """通过微信 code 换取 openid 和 session_key"""
    if not WECHAT_APPID or not WECHAT_SECRET:
        logger.error("WECHAT_APPID 或 WECHAT_SECRET 未配置")
        raise HTTPException(status_code=500, detail="微信服务配置缺失")

    import httpx
    import ssl

    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": WECHAT_APPID,
        "secret": WECHAT_SECRET,
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

class BindLoginResponse(BaseModel):
    token: str
    user_id: int
    phone_number: str
    openid: str
    nickname: Optional[str] = None
    is_new_user: bool = False

@app.post("/api/auth/bind", response_model=BindLoginResponse)
async def auth_bind_login(request: BindLoginRequest, session: Session = Depends(get_session)):
    """首次账密登录激活绑定"""
    account = request.account.strip()
    password = request.password.strip()
    code = request.code.strip()

    # --- 1. 参数校验 ---
    if not account or len(account) != 11 or not account.isdigit():
        raise HTTPException(status_code=400, detail="请输入正确的11位手机号")
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于6位")
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

    # --- 4. 建立 / 更新 OpenID 绑定（直接写 user 表）---
    # 若此 openid 此前已绑定到其他用户，先清空旧绑定（一对一约束）
    old_user = session.exec(
        select(User).where(User.openid == openid, User.id != user.id)
    ).first()
    if old_user:
        old_user.openid = None
        old_user.unionid = None
        old_user.session_key = None
        old_user.updated_at = int(time.time() * 1000)
        session.add(old_user)
        logger.info(f"openid 换绑：旧用户 {old_user.id} 的绑定已清除")

    # 对当前用户落库 openid
    user.openid = openid
    user.session_key = new_session_key
    if unionid:
        user.unionid = unionid
    user.updated_at = int(time.time() * 1000)
    session.add(user)
    session.commit()
    session.refresh(user)
    logger.info(f"OpenID 绑定完成: user_id={user.id}, openid={openid[-8:]}...")

    # --- 5. 签发 JWT ---
    token = create_jwt_token(user.id, user.phone_number, openid)

    return BindLoginResponse(
        token=token,
        user_id=user.id,
        phone_number=user.phone_number,
        openid=openid,
        nickname=user.nickname,
        is_new_user=is_new_user,
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

    # --- 4. 签发 JWT ---
    token = create_jwt_token(user.id, user.phone_number, openid)

    return SilentLoginResponse(
        token=token,
        user_id=user.id,
        phone_number=user.phone_number,
        openid=openid,
        nickname=user.nickname,
    )

# --- System Config APIs ---
class ConfigItem(BaseModel):
    key: str
    value: str
    is_encrypted: bool = False

class ConfigListResponse(BaseModel):
    configs: list[dict]
    has_required_configs: bool

@app.get("/api/config/list")
def get_config_list(session: Session = Depends(get_session)):
    """Get all config items (encrypted fields return empty string)"""
    try:
        configs = session.exec(select(SystemConfig).where(SystemConfig.is_active == True)).all()
        config_list = []
        for config in configs:
            config_list.append({"key": config.key, "value": config.value if not config.is_encrypted else "",
                                "is_encrypted": config.is_encrypted, "updated_at": config.updated_at})
        return ConfigListResponse(configs=config_list, has_required_configs=len(configs) > 0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取配置列表失败: {str(e)}")

@app.get("/api/config/{key}")
def get_config_value(key: str, session: Session = Depends(get_session)):
    """Get single config value (auto-decrypt)"""
    try:
        stmt = select(SystemConfig).where(
            SystemConfig.key == key,
            SystemConfig.is_active == True  # 只查询未删除的配置
        ).order_by(SystemConfig.id.desc())
        config = session.exec(stmt).first()
        if not config:
            raise HTTPException(status_code=404, detail=f"配置项 {key} 不存在")
        value = config.value
        if config.is_encrypted:
            value = ConfigEncryptor.decrypt(value)
        return {"key": key, "value": value, "is_encrypted": config.is_encrypted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")

@app.post("/api/config")
def save_config(config_item: ConfigItem, session: Session = Depends(get_session)):
    """Save config item (auto-encrypt sensitive info)"""
    try:
        sensitive_keys = ["ACCOUNT", "PASSWORD", "XIAOMI_ACCOUNT", "XIAOMI_PASSWORD"]
        should_encrypt = config_item.key in sensitive_keys or config_item.is_encrypted
        value_to_store = ConfigEncryptor.encrypt(config_item.value) if should_encrypt and config_item.value else config_item.value
        stmt = select(SystemConfig).where(
            SystemConfig.key == config_item.key,
            SystemConfig.is_active == True  # 只查询未删除的配置
        )
        existing_config = session.exec(stmt).first()
        if existing_config:
            existing_config.value = value_to_store
            existing_config.is_encrypted = should_encrypt
            existing_config.updated_at = int(time.time() * 1000)
        else:
            new_config = SystemConfig(key=config_item.key, value=value_to_store,
                                      is_encrypted=should_encrypt, is_active=True, updated_at=int(time.time() * 1000))
            session.add(new_config)
        session.commit()
        logger.info(f"Config saved: {config_item.key} (encrypted={should_encrypt})")
        return {"status": "success", "message": f"配置 {config_item.key} 已保存"}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}")

@app.delete("/api/config/{key}")
def delete_config(key: str, session: Session = Depends(get_session)):
    """Delete config item (soft delete)"""
    try:
        stmt = select(SystemConfig).where(
            SystemConfig.key == key,
            SystemConfig.is_active == True  # 只查询未删除的配置
        )
        config = session.exec(stmt).first()
        if not config:
            raise HTTPException(status_code=404, detail=f"配置项 {key} 不存在")
        # 软删除：设置is_active=False
        config.is_active = False
        config.updated_at = int(time.time() * 1000)
        session.commit()
        return {"status": "success", "message": f"配置 {key} 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"删除配置失败: {str(e)}")

async def push_to_xiaomi(record: WeightRecord, user: Optional[User] = None):
    """Async push weight data to Xiaomi Cloud (user-specific)"""
    try:
        from .utils.config_manager import get_config_from_db
        username = await get_config_from_db("account", user_id=record.user_id, platform="xiaomi")
        password = await get_config_from_db("password", user_id=record.user_id, platform="xiaomi")
        
        if not username or not password:
            logger.warning(f"Xiaomi credentials not configured for user {record.user_id}")
            return
        
        # Create temp service for this user
        import backend.app.services.xiaomi_service as xm_module
        temp_service = xm_module.XiaomiCloudService()
        temp_service.username = username
        temp_service.password = password
        
        initialized = await temp_service.initialize()
        if not initialized:
            logger.error(f"Xiaomi login failed for user {record.user_id}")
            return
        
        user_data = {"weight": record.weight, "impedance": record.impedance or 0, "user_id": record.user_id}
        if record.body_fat:
            user_data.update({"body_fat": record.body_fat, "bmi": record.bmi, "muscle": record.muscle,
                              "water": record.water, "visceral_fat": record.visceral_fat,
                              "bone_mass": record.bone_mass, "bmr": record.bmr})
        elif user:
            metrics = calculate_body_metrics(record.weight, record.impedance or 0, user)
            user_data.update(metrics)
        
        success = await temp_service.push_weight_data(user_data)
        if success:
            logger.info(f"Successfully pushed weight data to Xiaomi for user {record.user_id}")
        else:
            logger.error(f"Failed to push weight data to Xiaomi for user {record.user_id}")
    except Exception as e:
        logger.error(f"Error pushing to Xiaomi: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def _safe_create_push_task(record: WeightRecord, user: Optional[User] = None):
    """Safely create background task with error handling"""
    try:
        await push_to_xiaomi(record, user)
    except Exception as e:
        logger.error(f"Background push task failed: {e}")
# --- Device Management APIs ---
from .utils.config_manager import get_user_devices, add_device as add_device_to_db, delete_device

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

@app.post("/api/devices/add", response_model=DeviceResponse)
async def add_device_api(request: AddDeviceRequest, user_id: str):
    """Add device to user account with auto-login and token caching"""
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的user_id")
    try:
        device_key = await add_device_to_db(user_id=uid, platform=request.platform, account=request.account,
                                            password=request.password, device_name=request.device_name)
        await _init_service_for_user(request.platform, uid, request.account, request.password)
        
        # Clear dashboard cache for this user
        cache_prefix = f'user_{uid}'
        await cache_manager.delete(f'{cache_prefix}_dashboard_combined_data')
        logger.info(f"Cleared dashboard cache for user {uid}")
        
        return DeviceResponse(device_key=device_key, device_type=request.device_type,
                              device_name=request.device_name or f"{request.platform}_device",
                              platform=request.platform, status="active")
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
        success = await delete_device(uid, device_key)
        if not success:
            raise HTTPException(status_code=404, detail="设备不存在")
        
        # Clear all dashboard caches for this user
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
    xiaomi_account: Optional[str] = None
    xiaomi_password: Optional[str] = None

@app.get("/api/system/config", response_model=SystemConfigResponse)
async def get_system_config():
    """Get system configuration (passwords masked)"""
    try:
        # Get first user ID for each platform
        cp_user = await _get_first_user_with_platform("cloudpets")
        pk_user = await _get_first_user_with_platform("petkit")
        xm_user = await _get_first_user_with_platform("xiaomi")
        
        config = SystemConfigResponse()
        
        if cp_user:
            config.cloudpets_account = await get_config_from_db("account", user_id=cp_user, platform="cloudpets")
            has_pwd = await get_config_from_db("password", user_id=cp_user, platform="cloudpets")
            config.cloudpets_password = "********" if has_pwd else None
        
        if pk_user:
            config.petkit_account = await get_config_from_db("account", user_id=pk_user, platform="petkit")
            has_pwd = await get_config_from_db("password", user_id=pk_user, platform="petkit")
            config.petkit_password = "********" if has_pwd else None
        
        if xm_user:
            config.xiaomi_account = await get_config_from_db("account", user_id=xm_user, platform="xiaomi")
            has_pwd = await get_config_from_db("password", user_id=xm_user, platform="xiaomi")
            config.xiaomi_password = "********" if has_pwd else None
        
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
        
        # If user has Xiaomi configured, push data
        try:
            from .utils.config_manager import get_config_from_db
            xm_username = await get_config_from_db("account", user_id=member.user_id, platform="xiaomi")
            xm_password = await get_config_from_db("password", user_id=member.user_id, platform="xiaomi")
            if xm_username and xm_password:
                _track_task(_safe_create_push_task(record, member))
        except Exception as e:
            logger.error(f"Failed to check Xiaomi config: {e}")
        
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

