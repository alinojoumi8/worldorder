from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import redis.asyncio as redis

from polis.events.types import Event


def live_channel(run_id: UUID | str) -> str:
    return f"polis:run:{run_id}:live"


class RedisEphemeralPublisher:
    def __init__(
        self,
        url: str,
        run_id: UUID,
        *,
        max_queue: int = 1,
        rate_hz: int = 10,
    ) -> None:
        self.client = redis.from_url(url, decode_responses=True)
        self.run_id = run_id
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self.publish_interval_s = 1 / max(1, rate_hz)
        self.dropped = 0
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._drain())

    async def publish(self, events: Sequence[Event]) -> None:
        if not events:
            return
        frames: list[dict[str, Any]] = [
            {
                "op": "eph",
                "kind": event.kind,
                "tick": event.tick,
                "payload": event.payload,
            }
            for event in events
        ]
        if self.queue.full():
            previous = self.queue.get_nowait()
            self.queue.task_done()
            by_kind = {int(item["kind"]): item for item in previous["frames"]}
            by_kind.update({int(item["kind"]): item for item in frames})
            frames = list(by_kind.values())
            self.dropped += 1
        frame = {
            "op": "batch",
            "tick": max(int(item["tick"]) for item in frames),
            "frames": frames,
        }
        self.queue.put_nowait(frame)

    async def _drain(self) -> None:
        while True:
            frame = await self.queue.get()
            try:
                if self.dropped:
                    await self.client.publish(
                        live_channel(self.run_id),
                        json.dumps(
                            {
                                "op": "lag",
                                "dropped": self.dropped,
                                "reason": "publisher_backpressure",
                            },
                            separators=(",", ":"),
                        ),
                    )
                    self.dropped = 0
                await self.client.publish(
                    live_channel(self.run_id),
                    json.dumps(frame, separators=(",", ":")),
                )
            finally:
                self.queue.task_done()
            await asyncio.sleep(self.publish_interval_s)

    async def close(self) -> None:
        await self.queue.join()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.client.aclose()


@dataclass(slots=True)
class LiveClient:
    queue: asyncio.Queue[dict[str, Any]]
    channels: set[str] = field(default_factory=lambda: {"tick"})
    pins: set[str] = field(default_factory=set)
    dropped: int = 0


class LiveHub:
    def __init__(self, client: redis.Redis, *, ring_frames: int = 256) -> None:
        self.redis = client
        self.ring_frames = ring_frames
        self._clients: defaultdict[str, list[LiveClient]] = defaultdict(list)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def connect(self, run_id: str) -> LiveClient:
        client = LiveClient(asyncio.Queue(maxsize=self.ring_frames))
        self._clients[run_id].append(client)
        if run_id not in self._tasks:
            self._tasks[run_id] = asyncio.create_task(self._fanout(run_id))
        return client

    async def disconnect(self, run_id: str, client: LiveClient) -> None:
        clients = self._clients[run_id]
        if client in clients:
            clients.remove(client)
        if not clients:
            task = self._tasks.pop(run_id, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self._clients.pop(run_id, None)

    async def _fanout(self, run_id: str) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(live_channel(run_id))
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                frame = json.loads(str(message["data"]))
                for client in tuple(self._clients[run_id]):
                    if client.queue.full():
                        try:
                            client.queue.get_nowait()
                            client.queue.task_done()
                        except asyncio.QueueEmpty:
                            pass
                        client.dropped += 1
                    if client.dropped:
                        client.queue.put_nowait(
                            {
                                "op": "lag",
                                "dropped": client.dropped,
                                "reason": "backpressure",
                            }
                        )
                        client.dropped = 0
                        if client.queue.full():
                            continue
                    client.queue.put_nowait(frame)
        finally:
            await pubsub.unsubscribe(live_channel(run_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._clients.clear()


def apply_client_message(
    client: LiveClient,
    message: Mapping[str, Any],
    *,
    max_channels: int,
    max_pins: int,
) -> dict[str, Any] | None:
    op = message.get("op")
    if op == "subscribe":
        requested = {str(value) for value in message.get("channels", ())}
        client.channels = set(sorted(requested)[:max_channels])
    elif op == "unsubscribe":
        client.channels -= {str(value) for value in message.get("channels", ())}
    elif op == "pin":
        requested = {str(value) for value in message.get("agents", ())}
        client.pins = set(sorted(requested)[:max_pins])
    elif op == "ping":
        return {"op": "pong", "t": message.get("t")}
    return None
