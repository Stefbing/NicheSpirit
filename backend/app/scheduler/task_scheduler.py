from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable


class TaskScheduler:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    def every(self, seconds: int, job: Callable[[], Awaitable[None]]) -> None:
        async def runner() -> None:
            while True:
                await job()
                await asyncio.sleep(seconds)

        self._tasks.append(asyncio.create_task(runner()))

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
