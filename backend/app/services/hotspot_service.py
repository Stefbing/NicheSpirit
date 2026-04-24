from __future__ import annotations

from .demo_data import hotspot_items


class HotspotService:
    def __init__(self) -> None:
        self._items = hotspot_items()

    async def list_hotspots(self) -> list[dict]:
        return self._items
