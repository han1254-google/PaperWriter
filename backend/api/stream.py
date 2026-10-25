"""生成任务流式事件中心：job 内 publish，SSE 端点 subscribe（NDJSON）。"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator


class StreamHub:
    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue] = {}

    async def publish(self, job_id: int, event: dict[str, Any]) -> None:
        q = self._queues.get(job_id)
        if q is not None:
            await q.put(event)

    def subscribe(self, job_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[job_id] = q
        return q

    def close(self, job_id: int) -> None:
        self._queues.pop(job_id, None)


_hub: StreamHub | None = None


def get_hub() -> StreamHub:
    global _hub
    if _hub is None:
        _hub = StreamHub()
    return _hub


async def on_event_factory(job_id: int):
    """给任务用的 on_event 回调。"""
    async def on_event(event: dict[str, Any]) -> None:
        await get_hub().publish(job_id, event)
    return on_event


async def stream_events(job_id: int) -> AsyncIterator[dict[str, Any]]:
    """SSE 事件流。任务结束（done/error/任务状态终结）后收尾。"""
    import json
    from .. import db
    hub = get_hub()
    q = hub.subscribe(job_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                job = db.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))
                if not job or job["status"] in ("succeeded", "failed", "cancelled"):
                    yield {"type": "heartbeat_end"}
                    break
                yield {"type": "ping"}
                continue
            yield event
            if event.get("type") in ("done", "error"):
                break
    finally:
        hub.close(job_id)
