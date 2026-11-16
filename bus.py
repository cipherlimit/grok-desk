"""In-process asyncio bus: bus.scanner -> bus.auditor -> bus.analyst -> bus.executor -> risk."""
import asyncio
from collections import defaultdict

class Bus:
    def __init__(self):
        self.queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
    async def put(self, topic: str, msg: dict):
        await self.queues[topic].put(msg)
    async def get(self, topic: str) -> dict:
        return await self.queues[topic].get()

bus = Bus()
