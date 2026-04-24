from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    phone_number: str = Field(index=True, unique=True)
    nickname: str = Field(default="新用户")
    gender: str = Field(default="male")
    age: int = Field(default=25)
    height: int = Field(default=175)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Device(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    platform: str = Field(index=True)
    device_type: str = Field(index=True)
    device_name: str = Field(index=True)
    device_uid: str = Field(default="", index=True)
    source_type: str = Field(default="wifi", index=True)
    bind_status: str = Field(default="bound", index=True)
    supports_control: bool = Field(default=True)
    is_active: bool = Field(default=True)
    metadata_json: str = Field(default="{}")
    last_sync_at: Optional[datetime] = None
    last_error: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SystemConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    key: str = Field(index=True)
    value: str
    platform: str = Field(default="global", index=True)
    device_name: str = Field(default="")
    is_encrypted: bool = Field(default=False)
    updated_at: int = Field(default=0)


class WeightRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    weight: float
    impedance: Optional[int] = None
    bmi: Optional[float] = None
    body_fat: Optional[float] = None
    muscle: Optional[float] = None
    water: Optional[float] = None
    visceral_fat: Optional[float] = None
    bone_mass: Optional[float] = None
    bmr: Optional[float] = None
    timestamp: int = Field(index=True)
    xiaomi_pushed: bool = Field(default=False)
    xiaomi_push_time: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
