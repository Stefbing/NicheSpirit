from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from sqlmodel import Session, delete, select

from ..models.models import SystemConfig
from .config_encryptor import ConfigEncryptor


def get_config_from_db(session: Session, user_id: int, key: str, platform: str = "global", device_name: str = "") -> str | None:
    """从数据库获取配置值"""
    statement = select(SystemConfig).where(
        SystemConfig.user_id == user_id,
        SystemConfig.key == key,
        SystemConfig.platform == platform,
    )
    if device_name:
        statement = statement.where(SystemConfig.device_name == device_name)
    
    config = session.exec(statement).first()
    return config.value if config else None


def decrypt_value(encrypted_value: str) -> str:
    """解密配置值"""
    from .config_encryptor import ConfigEncryptor
    encryptor = ConfigEncryptor()
    return encryptor.decrypt(encrypted_value)


def encrypt_value(value: str) -> str:
    """加密配置值"""
    from .config_encryptor import ConfigEncryptor
    encryptor = ConfigEncryptor()
    return encryptor.encrypt(value)


class ConfigManager:
    def __init__(self, encryptor: ConfigEncryptor) -> None:
        self.encryptor = encryptor

    def list_configs(self, session: Session, user_id: int) -> list[SystemConfig]:
        return list(session.exec(select(SystemConfig).where(SystemConfig.user_id == user_id)))

    def upsert_config(
        self,
        session: Session,
        *,
        user_id: int,
        key: str,
        value: str,
        platform: str = "global",
        device_name: str = "",
        is_encrypted: bool = False,
    ) -> SystemConfig:
        statement = select(SystemConfig).where(
            SystemConfig.user_id == user_id,
            SystemConfig.key == key,
            SystemConfig.platform == platform,
            SystemConfig.device_name == device_name,
        )
        config = session.exec(statement).first()
        stored_value = self.encryptor.encrypt(value) if is_encrypted else value
        if config is None:
            config = SystemConfig(
                user_id=user_id,
                key=key,
                value=stored_value,
                platform=platform,
                device_name=device_name,
                is_encrypted=is_encrypted,
                updated_at=int(time.time() * 1000),
            )
            session.add(config)
        else:
            config.value = stored_value
            config.is_encrypted = is_encrypted
            config.updated_at = int(time.time() * 1000)
        session.commit()
        session.refresh(config)
        return config

    def delete_config(self, session: Session, user_id: int, key: str) -> None:
        session.exec(delete(SystemConfig).where(SystemConfig.user_id == user_id, SystemConfig.key == key))
        session.commit()

    def get_value(self, session: Session, *, user_id: int, key: str) -> str | None:
        config = session.exec(
            select(SystemConfig).where(SystemConfig.user_id == user_id, SystemConfig.key == key)
        ).first()
        if not config:
            return None
        return self.encryptor.decrypt(config.value) if config.is_encrypted else config.value

    def grouped_by_device(self, session: Session, user_id: int) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"credentials": {}})
        for item in self.list_configs(session, user_id):
            bucket = grouped[(item.platform, item.device_name or item.platform)]
            bucket["platform"] = item.platform
            bucket["device_name"] = item.device_name or item.platform
            bucket["credentials"][item.key] = "***" if item.is_encrypted else item.value
        return list(grouped.values())
