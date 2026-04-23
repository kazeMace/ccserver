"""
queue — QueueEmitter：基于 asyncio.Queue 的事件发射器。

用于后台 Agent，外部通过 queue 属性消费事件。
"""

import asyncio
from .base import BaseEmitter


class QueueEmitter(BaseEmitter):
    """
    基于 asyncio.Queue 的 Emitter，用于后台 Agent。
    外部通过 `queue` 属性消费事件。
    """

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: dict) -> None:
        """将事件放入队列。"""
        await self.queue.put(event)
