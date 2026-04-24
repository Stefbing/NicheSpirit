from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from .models.db import get_session, init_db
from .models.models import Device, User, WeightRecord
from .scheduler.task_scheduler import TaskScheduler
from .services.device_registry_service import DeviceRegistryService
from .services.cloudpets_service import CloudPetsService
from .services.demo_data import dashboard_snapshot, device_seed_rows, weight_history
from .services.hotspot_service import HotspotService
from .services.petkit_service import PetKitService
from .services.xiaomi_service import XiaomiCloudService
from .utils.cache_manager import CacheManager
from .utils.config_encryptor import ConfigEncryptor
from .utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

app = FastAPI(title="AutoHome", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

cache = CacheManager()
encryptor = ConfigEncryptor()
config_manager = ConfigManager(encryptor)
petkit_service = PetKitService()
cloudpets_service = CloudPetsService()
xiaomi_service = XiaomiCloudService()
device_registry = DeviceRegistryService()
hotspot_service = HotspotService()
scheduler = TaskScheduler()


def db_session() -> Session:
    with get_session() as session:
        yield session


SessionDep = Annotated[Session, Depends(db_session)]


def calculate_body_metrics(weight: float, impedance: int | None, user: User) -> dict:
    height_m = user.height / 100
    bmi = weight / (height_m**2)
    body_fat = 0.8 * bmi + 0.1 * user.age - 5.4 if user.gender == "male" else 0.8 * bmi + 0.1 * user.age + 4.1
    if impedance and impedance > 0:
        body_fat += (impedance - 500) / 100
    body_fat = max(5.0, min(body_fat, 45.0))
    muscle = weight * (1 - body_fat / 100) * 0.75
    water = (100 - body_fat) * 0.7
    visceral_fat = max(1.0, min(bmi - 13.0, 20.0))
    bone_mass = weight * 0.04
    bmr = weight * (24.0 if user.gender == "male" else 22.0)
    return {
        "bmi": round(bmi, 2),
        "body_fat": round(body_fat, 2),
        "muscle": round(muscle, 2),
        "water": round(water, 2),
        "visceral_fat": round(visceral_fat, 2),
        "bone_mass": round(bone_mass, 2),
        "bmr": round(bmr, 2),
    }


def dashboard_cache_key(user_id: int) -> str:
    return f"dashboard_combined_data:{user_id}"


def device_cache_key(user_id: int) -> str:
    return f"petkit_devices:{user_id}"


def services_cache_key(user_id: int) -> str:
    return f"cloudpets_servings:{user_id}"


def plans_cache_key(user_id: int) -> str:
    return f"cloudpets_plans:{user_id}"


def serialize_device(device: Device) -> dict:
    data = device_registry.serialize(device)
    data["metadata_json"] = data.get("metadata_json") or "{}"
    return data


async def build_dashboard_payload(session: Session, user_id: int) -> dict:
    user = session.get(User, user_id)
    devices = [serialize_device(device) for device in device_registry.list_devices(session, user_id)]
    device_index = {item["device_uid"] or f"{item['platform']}-{item['id']}": item for item in devices}
    hot_trends = await hotspot_service.list_hotspots()
    payload = dashboard_snapshot()
    payload["devices"] = devices
    payload["hotspots"] = hot_trends
    petkit_snapshot = await petkit_service.list_devices()
    payload["petkit_devices"] = [
        {
            **item,
            "name": device_index.get(item["id"], {}).get("device_name", item["name"]),
        }
        for item in petkit_snapshot
        if item["id"] in device_index
    ]
    cloudpets_device = next((item for item in devices if item["platform"] == "cloudpets"), None)
    if cloudpets_device:
        cloudpets_detail = await cloudpets_service.detail()
        cloudpets_detail["id"] = cloudpets_device.get("device_uid") or cloudpets_detail.get("id")
        cloudpets_detail["name"] = cloudpets_device.get("device_name", cloudpets_detail.get("name", "喂食器"))
        payload["cloudpets"] = cloudpets_detail
    else:
        payload["cloudpets"] = {
            "id": "",
            "name": "未绑定喂食器",
            "status": "offline",
            "food_level": 0,
            "battery": 0,
            "servings_today": 0,
            "plans": [],
        }
    xiaomi_bound = any(item["platform"] == "xiaomi" for item in devices)
    xiaomi_status = await xiaomi_service.status()
    payload["xiaomi"] = {
        **xiaomi_status,
        "connected": bool(xiaomi_bound and xiaomi_status.get("connected")),
        "message": xiaomi_status.get("message", "同步状态"),
    }
    latest_record = session.exec(
        select(WeightRecord).where(WeightRecord.user_id == user_id).order_by(WeightRecord.timestamp.desc())
    ).first()
    online_count = len([item for item in devices if item.get("is_active") and item.get("bind_status") == "bound"])
    petkit_visits = sum(device.get("stats", {}).get("today_visits", 0) for device in payload["petkit_devices"])
    payload["scene"] = {
        "title": "家庭控制中心已同步" if devices else "等待添加设备",
        "subtitle": f"当前在线 {online_count} 台设备，猫厕访问 {petkit_visits} 次，热点与缓存会自动刷新。",
    }
    payload["overview"] = [
        {"label": "在线设备", "value": online_count, "trend": f"{len(devices)} 台已绑定"},
        {"label": "今日喂食", "value": f"{payload['cloudpets'].get('servings_today', 0)} 次", "trend": "自动缓存"},
        {"label": "猫厕访问", "value": f"{petkit_visits} 次", "trend": "清理同步中"},
        {
            "label": "最近体重",
            "value": f"{latest_record.weight} kg" if latest_record else "--",
            "trend": datetime.fromtimestamp(latest_record.timestamp / 1000).strftime("%m-%d %H:%M")
            if latest_record
            else "暂无记录",
        },
    ]
    payload["user"] = user.model_dump(mode="json") if user else None
    payload["has_devices"] = bool(devices)
    payload["need_bind"] = not bool(devices)
    payload["has_configured"] = bool(config_manager.list_configs(session, user_id))
    return payload


def seed_data(session: Session) -> None:
    if session.exec(select(User)).first():
        user = session.exec(select(User).where(User.phone_number == "13800138000")).first()
        if user:
            device_registry.prune_platform_devices(session, user.id, "petkit", {"petkit-t4-01"})
            device_registry.upsert_seed_devices(session, user.id, device_seed_rows())
        return
    user = User(phone_number="13800138000", nickname="演示用户", gender="male", age=29, height=175)
    session.add(user)
    session.commit()
    session.refresh(user)
    for record in weight_history():
        session.add(
            WeightRecord(
                user_id=user.id,
                weight=record["weight"],
                bmi=record["bmi"],
                body_fat=record["body_fat"],
                muscle=record["muscle"],
                water=record["water"],
                timestamp=record["timestamp"],
                xiaomi_pushed=record["xiaomi_pushed"],
            )
        )
    config_manager.upsert_config(session, user_id=user.id, key="app_version", value="0.5.0")
    # PetKit 演示配置 (账号密码都是 demo-account)
    config_manager.upsert_config(
        session,
        user_id=user.id,
        key="account",
        value="demo-account",
        platform="petkit",
        device_name="主猫厕所",
        is_encrypted=True,
    )
    config_manager.upsert_config(
        session,
        user_id=user.id,
        key="password",
        value="demo-account",
        platform="petkit",
        device_name="主猫厕所",
        is_encrypted=True,
    )
    device_registry.prune_platform_devices(session, user.id, "petkit", {"petkit-t4-01"})
    device_registry.upsert_seed_devices(session, user.id, device_seed_rows())
    session.commit()


async def refresh_dashboard_cache(session: Session | None = None, user_id: int = 1) -> dict:
    if session is None:
        with get_session() as new_session:
            data = await build_dashboard_payload(new_session, user_id)
    else:
        data = await build_dashboard_payload(session, user_id)
    cache.set(dashboard_cache_key(user_id), data, ttl_seconds=60)
    cache.set(device_cache_key(user_id), data.get("petkit_devices", []), ttl_seconds=300)
    cache.set(services_cache_key(user_id), data.get("cloudpets", {}), ttl_seconds=120)
    cache.set(plans_cache_key(user_id), data.get("cloudpets", {}).get("plans", []), ttl_seconds=300)
    return data


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    with get_session() as session:
        seed_data(session)
        # 初始化 PetKit 服务
        try:
            petkit_initialized = await petkit_service.initialize(user_id=1, session=session)
            if petkit_initialized:
                logger.info("✓ PetKit 服务初始化成功")
            else:
                logger.warning("⚠ PetKit 服务未配置或初始化失败,使用演示数据")
        except Exception as e:
            logger.error(f"❌ PetKit 服务初始化异常: {e}")
        
        await refresh_dashboard_cache(session, user_id=1)
    scheduler.every(60, refresh_dashboard_cache)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await scheduler.shutdown()


@app.get("/")
async def web_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/auth/login")
async def login(session: SessionDep, payload: dict = Body(...)) -> dict:
    phone_number = payload.get("phone_number")
    if not phone_number:
        raise HTTPException(status_code=400, detail="phone_number is required")
    user = session.exec(select(User).where(User.phone_number == phone_number)).first()
    if user is None:
        user = User(
            phone_number=phone_number,
            nickname=payload.get("nickname", "新用户"),
            gender=payload.get("gender", "male"),
            age=payload.get("age", 25),
            height=payload.get("height", 175),
        )
        session.add(user)
    else:
        user.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(user)
    data = await refresh_dashboard_cache(session, user.id)
    return {
        "ok": True,
        "user": user.model_dump(mode="json"),
        "devices": data.get("devices", []),
        "dashboard": data,
        "has_configured": data.get("has_configured", False),
        "has_devices": data.get("has_devices", False),
        "need_bind": data.get("need_bind", True),
    }


@app.get("/api/auth/check-config")
async def check_config(session: SessionDep, user_id: int = 1) -> dict:
    devices = [serialize_device(device) for device in device_registry.list_devices(session, user_id)]
    return {
        "has_configured": len(config_manager.list_configs(session, user_id)) > 0,
        "devices": config_manager.grouped_by_device(session, user_id),
        "registered_devices": devices,
        "has_devices": bool(devices),
        "need_bind": not bool(devices),
    }


@app.post("/api/auth/reinit-services")
async def reinit_services() -> dict:
    await refresh_dashboard_cache(user_id=1)
    return {"ok": True, "message": "服务状态已刷新"}


@app.get("/api/devices/list")
async def list_devices(session: SessionDep, user_id: int = 1) -> list[dict]:
    return [serialize_device(device) for device in device_registry.list_devices(session, user_id)]


@app.post("/api/devices/add")
async def add_device(session: SessionDep, payload: dict = Body(...)) -> dict:
    required = ["user_id", "platform", "device_type", "device_name"]
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise HTTPException(status_code=400, detail=f"missing fields: {', '.join(missing)}")
    device = device_registry.add_device(session, payload)
    if device.platform == "petkit":
        petkit_service.register_device(
            {
                "id": device.device_uid or f"petkit-{device.id}",
                "name": device.device_name,
                "model": payload.get("model", "PetKit Device"),
                "status": "online" if device.is_active else "standby",
            }
        )
    if device.platform == "cloudpets":
        cloudpets_service.register_device(
            {
                "id": device.device_uid or f"cloudpets-{device.id}",
                "name": device.device_name,
                "status": "online" if device.is_active else "offline",
            }
        )
    await refresh_dashboard_cache(session, int(device.user_id))
    return {"ok": True, "device": serialize_device(device)}


@app.delete("/api/devices/{device_id}")
async def delete_device(session: SessionDep, device_id: int, user_id: int = 1) -> dict:
    device_registry.delete_device(session, user_id, device_id)
    await refresh_dashboard_cache(session, user_id)
    return {"ok": True, "deleted": device_id}


@app.get("/api/hot/trends")
async def hot_trends() -> dict:
    items = await hotspot_service.list_hotspots()
    return {"ok": True, "items": items, "source_count": len({item["source"] for item in items})}


@app.get("/api/petkit/devices")
async def petkit_devices_endpoint() -> list[dict]:
    cached = cache.get(device_cache_key(1))
    if cached:
        return cached
    return cache.set(device_cache_key(1), await petkit_service.list_devices(), ttl_seconds=300)


@app.get("/api/petkit/stats")
async def petkit_stats() -> list[dict]:
    return await petkit_service.stats()


@app.get("/api/petkit/devices-stats")
async def petkit_devices_stats() -> list[dict]:
    return await petkit_service.list_devices()


@app.post("/api/petkit/clean")
async def petkit_clean(payload: dict = Body(...)) -> dict:
    cache.delete(device_cache_key(1))
    cache.delete(dashboard_cache_key(1))
    try:
        result = await petkit_service.clean(payload["device_id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="device not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    await refresh_dashboard_cache(user_id=1)
    return result


@app.post("/api/petkit/deodorize")
async def petkit_deodorize(payload: dict = Body(...)) -> dict:
    cache.delete(device_cache_key(1))
    cache.delete(dashboard_cache_key(1))
    try:
        result = await petkit_service.deodorize(payload["device_id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="device not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    await refresh_dashboard_cache(user_id=1)
    return result


@app.get("/api/cloudpets/servings_today")
async def cloudpets_servings_today() -> dict:
    cached = cache.get(services_cache_key(1))
    if cached:
        return cached
    return cache.set(services_cache_key(1), await cloudpets_service.servings_today(), ttl_seconds=120)


@app.get("/api/cloudpets/plans")
async def cloudpets_plans() -> list[dict]:
    cached = cache.get(plans_cache_key(1))
    if cached:
        return cached
    return cache.set(plans_cache_key(1), await cloudpets_service.list_plans(), ttl_seconds=300)


@app.post("/api/cloudpets/feed")
async def cloudpets_feed(payload: dict = Body(...)) -> dict:
    cache.delete(services_cache_key(1))
    return await cloudpets_service.feed(int(payload.get("serving", 1)))


@app.post("/api/cloudpets/plans")
async def create_cloudpets_plan(payload: dict = Body(...)) -> dict:
    cache.delete(plans_cache_key(1))
    return await cloudpets_service.add_plan(
        {
            "id": payload.get("id", f"plan-{int(time.time())}"),
            "time": payload["time"],
            "serving": payload["serving"],
            "days": payload.get("days", [1, 2, 3, 4, 5, 6, 7]),
            "enable": payload.get("enable", True),
        }
    )


@app.put("/api/cloudpets/plans/{plan_id}")
async def update_cloudpets_plan(plan_id: str, payload: dict = Body(...)) -> dict:
    cache.delete(plans_cache_key(1))
    try:
        return await cloudpets_service.update_plan(plan_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="plan not found") from exc


@app.delete("/api/cloudpets/plans/{plan_id}")
async def delete_cloudpets_plan(plan_id: str) -> dict:
    cache.delete(plans_cache_key(1))
    return await cloudpets_service.delete_plan(plan_id)


@app.get("/api/xiaomi/status")
async def xiaomi_status() -> dict:
    return await xiaomi_service.status()


@app.post("/api/xiaomi/login")
async def xiaomi_login(payload: dict = Body(...)) -> dict:
    return await xiaomi_service.login(payload.get("account", "demo-account"))


@app.post("/api/xiaomi/push-weight")
async def xiaomi_push_weight(session: SessionDep, payload: dict = Body(...)) -> dict:
    record = session.get(WeightRecord, int(payload["record_id"]))
    if not record:
        raise HTTPException(status_code=404, detail="record not found")
    result = await xiaomi_service.push_weight(record.id)
    record.xiaomi_pushed = True
    record.xiaomi_push_time = datetime.utcnow()
    session.add(record)
    session.commit()
    return result


@app.get("/api/scale/history/{user_id}")
async def scale_history(session: SessionDep, user_id: int) -> list[dict]:
    statement = select(WeightRecord).where(WeightRecord.user_id == user_id).order_by(WeightRecord.timestamp.desc())
    return [record.model_dump() for record in session.exec(statement)]


@app.post("/api/scale/record")
async def scale_record(session: SessionDep, payload: dict = Body(...)) -> dict:
    user = session.get(User, int(payload["user_id"]))
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    record = WeightRecord(
        user_id=user.id,
        weight=payload["weight"],
        impedance=payload.get("impedance"),
        timestamp=payload.get("timestamp", int(time.time() * 1000)),
        **calculate_body_metrics(payload["weight"], payload.get("impedance"), user),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record.model_dump()


@app.get("/api/config/list")
async def config_list(session: SessionDep, user_id: int = 1) -> list[dict]:
    return [item.model_dump() for item in config_manager.list_configs(session, user_id)]


@app.get("/api/config/{key}")
async def config_get(session: SessionDep, key: str, user_id: int = 1) -> dict:
    value = config_manager.get_value(session, user_id=user_id, key=key)
    if value is None:
        raise HTTPException(status_code=404, detail="config not found")
    return {"key": key, "value": value}


@app.post("/api/config")
async def config_save(session: SessionDep, payload: dict = Body(...)) -> dict:
    config = config_manager.upsert_config(
        session,
        user_id=int(payload.get("user_id", 1)),
        key=payload["key"],
        value=payload["value"],
        platform=payload.get("platform", "global"),
        device_name=payload.get("device_name", ""),
        is_encrypted=payload.get("is_encrypted", False),
    )
    return config.model_dump()


@app.delete("/api/config/{key}")
async def config_delete(session: SessionDep, key: str, user_id: int = 1) -> dict:
    config_manager.delete_config(session, user_id, key)
    return {"ok": True, "deleted": key}


@app.get("/api/cache/status")
async def cache_status() -> list[dict]:
    return cache.status()


@app.post("/api/cache/refresh")
async def cache_refresh() -> dict:
    await refresh_dashboard_cache(user_id=1)
    return {"ok": True, "message": "缓存已刷新"}


@app.get("/api/dashboard/data")
async def dashboard_data(user_id: int = 1) -> dict:
    cached = cache.get(dashboard_cache_key(user_id))
    if cached:
        return cached
    await refresh_dashboard_cache(user_id=user_id)
    return cache.get(dashboard_cache_key(user_id))
