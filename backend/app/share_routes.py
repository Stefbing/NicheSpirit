"""设备分享 API 路由"""
import json, time, uuid, hashlib, logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

from .models.models import User, DeviceShare, SharedDeviceConfig, SystemConfig
from .models.db import get_session

router = APIRouter(prefix="/api/share", tags=["share"])

# ──── Request / Response Models ────

class CreateShareRequest(BaseModel):
    from_user_id: int
    device_keys: list[str]

class CreateShareResponse(BaseModel):
    share_id: int
    share_token: str
    share_link: str
    expires_at: int
    expire_duration_hours: int = 24

class AcceptShareRequest(BaseModel):
    share_token: str
    to_user_id: int

class UpdateExpiryRequest(BaseModel):
    share_id: int
    user_id: int          # 分享者（权限校验）
    expire_hours: int     # 新的有效时长（小时），从 accepted_at 开始计算

class CheckExpiredRequest(BaseModel):
    user_id: int          # 指定用户，0=检查所有

# ──── 辅助函数 ────

def _generate_token() -> str:
    raw = f"{time.time()}{uuid.uuid4().hex}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _parse_device_keys(device_keys: list[str]) -> set:
    platforms = set()
    for key in device_keys:
        parts = key.split('_', 1)
        if parts:
            platforms.add(parts[0])
    return platforms

def _get_device_configs_by_platforms(session: Session, user_id: int, platforms: set) -> dict:
    if not platforms:
        return {}
    configs = session.exec(
        select(SystemConfig).where(
            SystemConfig.user_id == user_id,
            SystemConfig.key.in_(["account", "password"]),
            SystemConfig.platform.in_(list(platforms)),
            SystemConfig.is_active == True
        )
    ).all()
    result = {}
    for cfg in configs:
        plat = cfg.platform
        if plat not in result:
            result[plat] = {"account": "", "password": ""}
        if cfg.key == "account":
            from .utils.config_encryptor import ConfigEncryptor
            result[plat]["account"] = ConfigEncryptor.decrypt(cfg.value) if cfg.is_encrypted else cfg.value
        elif cfg.key == "password":
            from .utils.config_encryptor import ConfigEncryptor
            result[plat]["password"] = ConfigEncryptor.decrypt(cfg.value) if cfg.is_encrypted else cfg.value
    return result

def _format_share_output(s: DeviceShare) -> dict:
    """统一格式化分享记录输出"""
    return {
        "id": s.id,
        "share_token": s.share_token,
        "status": s.status,
        "device_keys": json.loads(s.device_keys) if s.device_keys else [],
        "from_user_id": s.from_user_id,
        "to_user_id": s.to_user_id,
        "created_at": s.created_at,
        "accepted_at": s.accepted_at,
        "expires_at": s.expires_at,
        "expire_duration_hours": getattr(s, 'expire_duration_hours', 24),
    }

# ──── API Endpoints ────

@router.post("/create", response_model=CreateShareResponse)
async def create_share(request: CreateShareRequest, session: Session = Depends(get_session)):
    """用户A创建分享 - 默认24小时有效期"""
    user = session.get(User, request.from_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="分享者不存在")
    if not request.device_keys:
        raise HTTPException(status_code=400, detail="请选择要分享的设备")

    now_ms = int(time.time() * 1000)
    token = _generate_token()

    share = DeviceShare(
        from_user_id=request.from_user_id,
        share_token=token,
        status="pending",
        device_keys=json.dumps(request.device_keys),
        created_at=now_ms,
        expires_at=now_ms + 24 * 3600 * 1000,  # 链接有效期 24h
    )
    session.add(share)
    session.commit()
    session.refresh(share)

    return CreateShareResponse(
        share_id=share.id,
        share_token=token,
        share_link=f"pages/index/index?share_token={token}",
        expires_at=share.expires_at,
        expire_duration_hours=24,
    )


@router.post("/accept")
async def accept_share(request: AcceptShareRequest, session: Session = Depends(get_session)):
    """
    用户B接受分享
    - 检查链接是否过期（created_at + 24h）
    - 接受后 expires_at 重置为 accepted_at + 24h（设备可用时效）
    """
    share = session.exec(
        select(DeviceShare).where(
            DeviceShare.share_token == request.share_token,
            DeviceShare.status == "pending"
        )
    ).first()

    if not share:
        raise HTTPException(status_code=404, detail="分享链接无效或已过期")

    now_ms = int(time.time() * 1000)
    # 链接有效期 = created_at + 24h
    link_expiry = share.created_at + 24 * 3600 * 1000
    if now_ms > link_expiry:
        share.status = "revoked"
        session.add(share)
        session.commit()
        raise HTTPException(status_code=400, detail="分享链接已过期")

    to_user = session.get(User, request.to_user_id)
    if not to_user:
        raise HTTPException(status_code=404, detail="接受者用户不存在")
    if share.from_user_id == request.to_user_id:
        raise HTTPException(status_code=400, detail="不能接受自己的分享")

    device_keys = json.loads(share.device_keys)
    platforms = _parse_device_keys(device_keys)
    from_configs = _get_device_configs_by_platforms(session, share.from_user_id, platforms)

    logger.info(f'[AcceptShare] token={request.share_token} from={share.from_user_id} to={request.to_user_id} '
                f'platforms={platforms} from_configs={list(from_configs.keys())}')

    created_configs = []
    DEFAULT_DURATION_HOURS = 24

    # 处理云平台（petkit/cloudpets）— 须有 account/password 凭据
    for platform, creds in from_configs.items():
        if not creds["account"] or not creds["password"]:
            continue

        shared_account = f"{creds['account']}_shared_{request.to_user_id}"

        for dk in device_keys:
            # 只创建匹配当前平台的 device_key 记录
            dk_platform = dk.split('_', 1)[0] if '_' in dk else ''
            if dk_platform != platform:
                continue
            sc = SharedDeviceConfig(
                share_id=share.id,
                to_user_id=request.to_user_id,
                platform=platform,
                device_key=dk,
                config_account=shared_account,
                config_password=creds["password"],
                created_at=now_ms,
            )
            session.add(sc)
            created_configs.append({"device_key": dk, "platform": platform})

    # 处理 BLE 设备（xiaomi）— 无 account/password，仅记录设备存在
    ble_platforms = {'xiaomi'}
    for bp in ble_platforms:
        if bp not in platforms:
            continue
        for dk in device_keys:
            dk_platform = dk.split('_', 1)[0] if '_' in dk else ''
            if dk_platform != bp:
                continue
            sc = SharedDeviceConfig(
                share_id=share.id,
                to_user_id=request.to_user_id,
                platform=bp,
                device_key=dk,
                config_account='shared_ble',
                config_password='',
                created_at=now_ms,
            )
            session.add(sc)
            created_configs.append({"device_key": dk, "platform": bp})

    # 更新分享记录：接受后将 expires_at 设为设备可用时效（accepted_at + 24h）
    share.to_user_id = request.to_user_id
    share.status = "accepted"
    share.accepted_at = now_ms
    share.expires_at = now_ms + DEFAULT_DURATION_HOURS * 3600 * 1000
    session.add(share)
    session.commit()

    # 【诊断】确认数据库状态
    logger.info(f'[AcceptShare] ✅ 已完成: share_id={share.id} status={share.status} '
                f'to_user={share.to_user_id} configs_created={len(created_configs)} '
                f'configs={created_configs}')

    # 清除接受者的设备缓存 + 共享凭据缓存
    try:
        from .utils.device_cache import device_cache
        await device_cache.invalidate_user(request.to_user_id)
    except Exception:
        pass
    try:
        from .utils.redis_cache import redis_cache
        await redis_cache.delete(f"shared_creds:user_{request.to_user_id}")
    except Exception:
        pass

    return {
        "success": True,
        "message": f"分享接受成功，设备可用时长为 {DEFAULT_DURATION_HOURS} 小时",
        "configured": created_configs,
        "expire_duration_hours": DEFAULT_DURATION_HOURS,
    }


@router.get("/manage-list")
async def manage_shares(user_id: int, session: Session = Depends(get_session)):
    """
    获取分享者（A）的分享管理列表
    含被分享者昵称、各平台名称、剩余有效时长
    """
    shares = session.exec(
        select(DeviceShare).where(
            DeviceShare.from_user_id == user_id
        ).order_by(DeviceShare.created_at.desc())
    ).all()

    result = []
    for s in shares:
        item = _format_share_output(s)
        # 补充被分享者昵称
        if s.to_user_id:
            to_user = session.get(User, s.to_user_id)
            item["to_user_nickname"] = to_user.nickname if to_user else "未知用户"
        else:
            item["to_user_nickname"] = "待接受"

        # 计算剩余有效时长（基于 accepted_at + expire_duration_hours）
        if s.status == "accepted" and s.accepted_at:
            effective_expiry = s.expires_at  # 已在 accept 或 update-expiry 中设置
            remaining_ms = max(0, effective_expiry - int(time.time() * 1000))
            item["remaining_hours"] = round(remaining_ms / (3600 * 1000), 1)
        elif s.status == "pending":
            remaining_ms = max(0, s.expires_at - int(time.time() * 1000))
            item["remaining_hours"] = round(remaining_ms / (3600 * 1000), 1)
        else:
            item["remaining_hours"] = 0

        result.append(item)

    return {"shares": result}


@router.post("/update-expiry")
async def update_share_expiry(request: UpdateExpiryRequest, session: Session = Depends(get_session)):
    """
    分享者（A）修改已接受分享的设备可用时效
    """
    if request.expire_hours < 1 or request.expire_hours > 720:  # 1h ~ 30天
        raise HTTPException(status_code=400, detail="有效时长为 1~720 小时（30天）")

    share = session.get(DeviceShare, request.share_id)
    if not share:
        raise HTTPException(status_code=404, detail="分享记录不存在")
    if share.from_user_id != request.user_id:
        raise HTTPException(status_code=403, detail="仅分享者可修改时效")
    if share.status != "accepted":
        raise HTTPException(status_code=400, detail="仅已接受的分享可修改时效")
    if not share.accepted_at:
        raise HTTPException(status_code=400, detail="分享尚未被接受")

    new_expiry = share.accepted_at + request.expire_hours * 3600 * 1000
    now_ms = int(time.time() * 1000)
    if new_expiry <= now_ms:
        # 如果新设的时效已过期，直接撤销分享
        share.status = "revoked"
        share.expires_at = now_ms
        session.add(share)
        session.commit()

        # 清除被分享者缓存
        if share.to_user_id:
            try:
                from .utils.device_cache import device_cache
                await device_cache.invalidate_user(share.to_user_id)
                from .utils.redis_cache import redis_cache
                await redis_cache.delete(f"shared_creds:user_{share.to_user_id}")
            except Exception:
                pass

        return {
            "success": True,
            "message": "设置的时效已过期，分享已自动撤销",
            "status": "revoked",
        }

    share.expires_at = new_expiry
    session.add(share)
    session.commit()

    # 清除被分享者缓存
    if share.to_user_id:
        try:
            from .utils.device_cache import device_cache
            await device_cache.invalidate_user(share.to_user_id)
            from .utils.redis_cache import redis_cache
            await redis_cache.delete(f"shared_creds:user_{share.to_user_id}")
        except Exception:
            pass

    return {
        "success": True,
        "message": f"设备可用时效已更新为 {request.expire_hours} 小时",
        "expires_at": new_expiry,
        "expire_hours": request.expire_hours,
    }


@router.post("/check-expired")
async def check_expired_shares(request: CheckExpiredRequest, session: Session = Depends(get_session)):
    """
    扫描并自动撤销已过期的分享（后台定时任务调用）
    user_id=0 表示扫描所有用户
    """
    now_ms = int(time.time() * 1000)
    query = select(DeviceShare).where(
        DeviceShare.status.in_(["accepted", "pending"]),
        DeviceShare.expires_at <= now_ms,
    )
    if request.user_id > 0:
        query = query.where(DeviceShare.from_user_id == request.user_id)

    expired = session.exec(query).all()
    if not expired:
        return {"success": True, "revoked_count": 0, "message": "无过期分享"}

    revoked_ids = []
    to_users = set()
    for share in expired:
        share.status = "revoked"
        session.add(share)
        if share.to_user_id:
            to_users.add(share.to_user_id)
        revoked_ids.append(share.id)

    session.commit()

    # 清除所有受影响的被分享者缓存
    try:
        from .utils.device_cache import device_cache
        from .utils.redis_cache import redis_cache
        for uid in to_users:
            await device_cache.invalidate_user(uid)
            await redis_cache.delete(f"shared_creds:user_{uid}")
    except Exception:
        pass

    return {
        "success": True,
        "revoked_count": len(revoked_ids),
        "revoked_ids": revoked_ids,
        "message": f"已撤销 {len(revoked_ids)} 个过期分享",
    }


@router.get("/list")
async def list_shares(user_id: int, role: str = "from", session: Session = Depends(get_session)):
    """查询分享记录（兼容旧版）"""
    if role == "from":
        shares = session.exec(
            select(DeviceShare).where(
                DeviceShare.from_user_id == user_id
            ).order_by(DeviceShare.created_at.desc())
        ).all()
    else:
        shares = session.exec(
            select(DeviceShare).where(
                DeviceShare.to_user_id == user_id
            ).order_by(DeviceShare.created_at.desc())
        ).all()

    return {"shares": [_format_share_output(s) for s in shares]}


@router.get("/pending-from-user")
async def pending_share_from_user(from_user_id: int, session: Session = Depends(get_session)):
    """
    查询指定分享者最新的 pending 分享（from_uid 兜底方案）
    当 share_token 在入口参数中丢失时，通过 from_user_id 反查分享记录
    """
    now_ms = int(time.time() * 1000)
    share = session.exec(
        select(DeviceShare).where(
            DeviceShare.from_user_id == from_user_id,
            DeviceShare.status == "pending",
        ).order_by(DeviceShare.created_at.desc())
    ).first()

    if not share:
        return {"found": False, "share": None}

    # 检查链接是否过期
    link_expiry = share.created_at + 24 * 3600 * 1000
    if now_ms > link_expiry:
        share.status = "revoked"
        session.add(share)
        session.commit()
        return {"found": False, "share": None}

    return {
        "found": True,
        "share": _format_share_output(share),
    }


@router.post("/revoke")
async def revoke_share(share_id: int, user_id: int, session: Session = Depends(get_session)):
    """分享者撤销分享"""
    share = session.get(DeviceShare, share_id)
    if not share or share.from_user_id != user_id:
        raise HTTPException(status_code=404, detail="分享记录不存在")

    share.status = "revoked"
    session.add(share)
    session.commit()

    if share.to_user_id:
        try:
            from .utils.device_cache import device_cache
            await device_cache.invalidate_user(share.to_user_id)
        except Exception:
            pass
        try:
            from .utils.redis_cache import redis_cache
            await redis_cache.delete(f"shared_creds:user_{share.to_user_id}")
        except Exception:
            pass

    return {"success": True, "message": "分享已撤销"}
