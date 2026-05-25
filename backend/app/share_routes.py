"""设备分享 API 路由"""
import json, time, uuid, hashlib
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from .models.models import User, DeviceShare, SharedDeviceConfig, SystemConfig
from .models.db import get_session

router = APIRouter(prefix="/api/share", tags=["share"])

# ──── Request / Response Models ────

class CreateShareRequest(BaseModel):
    from_user_id: int
    device_keys: list[str]        # 要分享的设备 key 列表

class CreateShareResponse(BaseModel):
    share_id: int
    share_token: str
    share_link: str
    expires_at: int

class AcceptShareRequest(BaseModel):
    share_token: str
    to_user_id: int               # 接受者的 user_id（由 wx.login 换取 openid 后获取）

class SharedDeviceInfo(BaseModel):
    device_key: str
    device_name: str
    platform: str
    from_user_name: str

class ShareListResponse(BaseModel):
    shares: list[dict]

# ──── 辅助函数 ────

def _generate_token() -> str:
    """生成 32 位分享令牌"""
    raw = f"{time.time()}{uuid.uuid4().hex}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _get_device_configs(session: Session, user_id: int, device_keys: list[str]) -> dict:
    """获取某个用户的设备配置（按 device_key 过滤）"""
    configs = session.exec(
        select(SystemConfig).where(
            SystemConfig.user_id == user_id,
            SystemConfig.key.in_(["account", "password"]),
            SystemConfig.is_active == True
        )
    ).all()

    # 按 platform 分组
    result = {}
    for cfg in configs:
        plat = cfg.platform
        if plat not in result:
            result[plat] = {"account": "", "password": ""}
        if cfg.key == "account":
            result[plat]["account"] = cfg.value
        elif cfg.key == "password":
            result[plat]["password"] = cfg.value
    return result

# ──── API Endpoints ────

@router.post("/create", response_model=CreateShareResponse)
async def create_share(request: CreateShareRequest, session: Session = Depends(get_session)):
    """用户A创建分享"""
    # 验证分享者存在
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
        expires_at=now_ms + 24 * 3600 * 1000,  # 24h 过期
    )
    session.add(share)
    session.commit()
    session.refresh(share)

    return CreateShareResponse(
        share_id=share.id,
        share_token=token,
        share_link=f"pages/index/index?share_token={token}",
        expires_at=share.expires_at,
    )


@router.post("/accept")
async def accept_share(request: AcceptShareRequest, session: Session = Depends(get_session)):
    """用户B接受分享 — 自动配置设备"""
    share = session.exec(
        select(DeviceShare).where(
            DeviceShare.share_token == request.share_token,
            DeviceShare.status == "pending"
        )
    ).first()

    if not share:
        raise HTTPException(status_code=404, detail="分享链接无效或已过期")

    now_ms = int(time.time() * 1000)
    if now_ms > share.expires_at:
        share.status = "revoked"
        session.add(share)
        session.commit()
        raise HTTPException(status_code=400, detail="分享链接已过期")

    # 检查接受者
    to_user = session.get(User, request.to_user_id)
    if not to_user:
        raise HTTPException(status_code=404, detail="接受者用户不存在")

    # 禁止自己分享给自己
    if share.from_user_id == request.to_user_id:
        raise HTTPException(status_code=400, detail="不能接受自己的分享")

    device_keys = json.loads(share.device_keys)

    # 获取分享者的设备配置（读取明文 password 用于生成被分享者配置）
    from_configs = _get_device_configs(session, share.from_user_id, device_keys)

    created_configs = []
    for platform, creds in from_configs.items():
        if not creds["account"] or not creds["password"]:
            continue

        # 为被分享者生成独立的凭证标识（追加 _shared 后缀避免重复）
        shared_account = f"{creds['account']}_shared_{request.to_user_id}"

        # 写入被分享者的 systemconfig
        for key, val in [("account", shared_account), ("password", creds["password"])]:
            existing = session.exec(
                select(SystemConfig).where(
                    SystemConfig.user_id == request.to_user_id,
                    SystemConfig.platform == platform,
                    SystemConfig.key == key,
                    SystemConfig.is_active == True
                )
            ).first()

            if existing:
                existing.value = val
                existing.updated_at = now_ms
                session.add(existing)
            else:
                session.add(SystemConfig(
                    user_id=request.to_user_id,
                    key=key,
                    value=val,
                    platform=platform,
                    device_name=f"shared_{platform}",
                    is_active=True,
                    updated_at=now_ms,
                ))

        # 记录共享配置映射
        for dk in device_keys:
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

    # 更新分享记录
    share.to_user_id = request.to_user_id
    share.status = "accepted"
    share.accepted_at = now_ms
    session.add(share)
    session.commit()

    return {
        "success": True,
        "message": "设备已自动配置完成",
        "configured": created_configs,
    }


@router.get("/list")
async def list_shares(user_id: int, role: str = "from", session: Session = Depends(get_session)):
    """查询分享记录：role=from 查出我分享的，role=to 查出我接受的"""
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

    result = []
    for s in shares:
        result.append({
            "id": s.id,
            "share_token": s.share_token,
            "status": s.status,
            "device_keys": json.loads(s.device_keys) if s.device_keys else [],
            "from_user_id": s.from_user_id,
            "to_user_id": s.to_user_id,
            "created_at": s.created_at,
            "accepted_at": s.accepted_at,
            "expires_at": s.expires_at,
        })

    return {"shares": result}


@router.post("/revoke")
async def revoke_share(share_id: int, user_id: int, session: Session = Depends(get_session)):
    """分享者撤销分享"""
    share = session.get(DeviceShare, share_id)
    if not share or share.from_user_id != user_id:
        raise HTTPException(status_code=404, detail="分享记录不存在")

    share.status = "revoked"
    session.add(share)
    session.commit()

    return {"success": True, "message": "分享已撤销"}
