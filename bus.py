"""In-process asyncio bus. Topics: auditor, analyst, executor, risk, cb.executor, cb.risk."""
import asyncio
from collections import defaultdict

class Bus:
    def __init__(self):
        self.queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
    async def put(self, topic: str, msg: dict):
        await self.queues[topic].put(msg)
    def put_nowait(self, topic: str, msg: dict):
        self.queues[topic].put_nowait(msg)
    async def get(self, topic: str) -> dict:
        return await self.queues[topic].get()

bus = Bus()
