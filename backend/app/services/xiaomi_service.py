from __future__ import annotations

from datetime import datetime


class XiaomiCloudService:
    def __init__(self) -> None:
        self._logged_in = False
        self._last_sync_at: str | None = None

    async def status(self) -> dict:
        return {
            "connected": self._logged_in,
            "last_sync_at": self._last_sync_at,
            "message": "已接入演示服务，后续可替换为真实小米云实现",
        }

    async def login(self, account: str) -> dict:
        self._logged_in = True
        self._last_sync_at = datetime.now().isoformat(timespec="seconds")
        return {"ok": True, "account": account, "logged_in": True}

    async def push_weight(self, record_id: int) -> dict:
        self._last_sync_at = datetime.now().isoformat(timespec="seconds")
        return {"ok": True, "record_id": record_id, "pushed_at": self._last_sync_at}

