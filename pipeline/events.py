import asyncio
from typing import AsyncIterator


class EventBus:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queues: dict[str, asyncio.Queue] = {}

    def _queue_for(self, run_id: str) -> asyncio.Queue:
        if run_id not in self._queues:
            self._queues[run_id] = asyncio.Queue()
        return self._queues[run_id]

    def publish(self, run_id: str, stage: str, payload: dict) -> None:
        queue = self._queue_for(run_id)
        self._loop.call_soon_threadsafe(queue.put_nowait, {"stage": stage, "payload": payload})

    async def subscribe(self, run_id: str) -> AsyncIterator[dict]:
        queue = self._queue_for(run_id)
        while True:
            event = await queue.get()
            yield event
            if event["stage"] == "__done__":
                break
