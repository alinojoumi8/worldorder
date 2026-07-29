"""Per-connection WebSocket buffering that can never backpressure the engine."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from typing import Any


class AgentConnectionLimiter:
    def __init__(self, max_per_agent: int) -> None:
        if max_per_agent < 1:
            raise ValueError("max_per_agent must be positive")
        self._max_per_agent = max_per_agent
        self._counts: dict[str, int] = {}

    def acquire(self, agent_id: str) -> bool:
        current = self._counts.get(agent_id, 0)
        if current >= self._max_per_agent:
            return False
        self._counts[agent_id] = current + 1
        return True

    def release(self, agent_id: str) -> None:
        current = self._counts.get(agent_id, 0)
        if current <= 1:
            self._counts.pop(agent_id, None)
        else:
            self._counts[agent_id] = current - 1


class BoundedFrameBuffer:
    def __init__(self, max_frames: int = 64) -> None:
        if max_frames < 1:
            raise ValueError("max_frames must be positive")
        self._max_frames = max_frames
        self._frames: deque[dict[str, Any]] = deque()
        self._condition = asyncio.Condition()
        self._dropped_observations = 0
        self._dropped_frames = 0
        self._closed = False

    @property
    def depth(self) -> int:
        return len(self._frames)

    async def put(self, frame: Mapping[str, Any]) -> None:
        buffered = dict(frame)
        async with self._condition:
            if self._closed:
                return
            if len(self._frames) >= self._max_frames:
                if self._drop_oldest_observation():
                    self._dropped_observations += 1
                elif buffered.get("type") == "observation":
                    self._dropped_observations += 1
                    self._condition.notify()
                    return
                else:
                    self._frames.popleft()
                    self._dropped_frames += 1
            self._frames.append(buffered)
            self._condition.notify()

    async def get(self) -> dict[str, Any] | None:
        async with self._condition:
            await self._condition.wait_for(
                lambda: (
                    self._closed
                    or self._dropped_observations > 0
                    or self._dropped_frames > 0
                    or bool(self._frames)
                )
            )
            if self._dropped_observations or self._dropped_frames:
                dropped_observations = self._dropped_observations
                dropped_frames = self._dropped_frames
                self._dropped_observations = 0
                self._dropped_frames = 0
                tick = int(self._frames[0].get("tick", 0)) if self._frames else 0
                return {
                    "type": "notice",
                    "tick": tick,
                    "notice": "degraded",
                    "detail": {
                        "reason": "backpressure",
                        "observations_dropped": dropped_observations,
                        "frames_dropped": dropped_frames,
                    },
                }
            if self._frames:
                frame = self._frames.popleft()
                self._condition.notify_all()
                return frame
            return None

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _drop_oldest_observation(self) -> bool:
        for index, frame in enumerate(self._frames):
            if frame.get("type") == "observation":
                del self._frames[index]
                return True
        return False
