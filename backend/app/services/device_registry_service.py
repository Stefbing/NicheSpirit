from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, delete, select

from ..models.models import Device


class DeviceRegistryService:
    def list_devices(self, session: Session, user_id: int) -> list[Device]:
        statement = select(Device).where(Device.user_id == user_id).order_by(Device.created_at.desc())
        return list(session.exec(statement))

    def list_by_platform(self, session: Session, user_id: int, platform: str) -> list[Device]:
        statement = select(Device).where(Device.user_id == user_id, Device.platform == platform).order_by(Device.created_at.desc())
        return list(session.exec(statement))

    def add_device(self, session: Session, payload: dict[str, Any]) -> Device:
        now = datetime.utcnow()
        device = Device(
            user_id=int(payload["user_id"]),
            platform=payload["platform"],
            device_type=payload["device_type"],
            device_name=payload["device_name"],
            device_uid=payload.get("device_uid", ""),
            source_type=payload.get("source_type", "wifi"),
            bind_status=payload.get("bind_status", "bound"),
            supports_control=bool(payload.get("supports_control", True)),
            is_active=bool(payload.get("is_active", True)),
            metadata_json=payload.get("metadata_json", "{}"),
            last_sync_at=payload.get("last_sync_at"),
            last_error=payload.get("last_error", ""),
            created_at=payload.get("created_at", now),
            updated_at=now,
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        return device

    def delete_device(self, session: Session, user_id: int, device_id: int) -> None:
        session.exec(delete(Device).where(Device.user_id == user_id, Device.id == device_id))
        session.commit()

    def upsert_seed_devices(self, session: Session, user_id: int, rows: list[dict[str, Any]]) -> list[Device]:
        created: list[Device] = []
        existing = {device.device_uid for device in self.list_devices(session, user_id)}
        for row in rows:
            if row["device_uid"] in existing:
                continue
            created.append(self.add_device(session, {"user_id": user_id, **row}))
        return created

    def prune_platform_devices(self, session: Session, user_id: int, platform: str, allowed_uids: set[str]) -> None:
        statement = select(Device).where(Device.user_id == user_id, Device.platform == platform)
        for device in session.exec(statement):
            if device.device_uid not in allowed_uids:
                session.delete(device)
        session.commit()

    def serialize(self, device: Device) -> dict[str, Any]:
        data = device.model_dump()
        if isinstance(data.get("last_sync_at"), datetime):
            data["last_sync_at"] = data["last_sync_at"].isoformat(timespec="seconds")
        if isinstance(data.get("created_at"), datetime):
            data["created_at"] = data["created_at"].isoformat(timespec="seconds")
        if isinstance(data.get("updated_at"), datetime):
            data["updated_at"] = data["updated_at"].isoformat(timespec="seconds")
        return data
