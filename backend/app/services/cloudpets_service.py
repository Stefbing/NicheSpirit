from __future__ import annotations

from .demo_data import cloudpets_device


class CloudPetsService:
    def __init__(self) -> None:
        self._device = cloudpets_device()

    def register_device(self, device: dict) -> None:
        self._device.setdefault("id", device.get("id", "cloudpets-feeder-01"))
        self._device["id"] = device.get("id", self._device["id"])
        self._device["name"] = device.get("name", self._device.get("name", "主喂食器"))
        self._device["status"] = device.get("status", self._device.get("status", "online"))

    async def servings_today(self) -> dict:
        return {"device_id": self._device["id"], "servings_today": self._device["servings_today"]}

    async def feed(self, serving: int) -> dict:
        self._device["servings_today"] += serving
        return {"ok": True, "message": f"已投喂 {serving} 份", "servings_today": self._device["servings_today"]}

    async def list_plans(self) -> list[dict]:
        return self._device["plans"]

    async def add_plan(self, plan: dict) -> dict:
        self._device["plans"].append(plan)
        return plan

    async def update_plan(self, plan_id: str, payload: dict) -> dict:
        for plan in self._device["plans"]:
            if plan["id"] == plan_id:
                plan.update(payload)
                return plan
        raise KeyError(plan_id)

    async def delete_plan(self, plan_id: str) -> dict:
        self._device["plans"] = [plan for plan in self._device["plans"] if plan["id"] != plan_id]
        return {"ok": True, "deleted_id": plan_id}

    async def detail(self) -> dict:
        return self._device
