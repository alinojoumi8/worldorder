"""In-process request, slot, and abuse accounting for one gateway instance."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from polis.gateway.errors import ErrorCode, ProtocolError

Bucket = Literal["request", "recall", "history", "memory"]
Trigger = Literal["schema", "signature", "rate"]


@dataclass(frozen=True, slots=True)
class LimitConfig:
    requests_per_tick: int = 40
    requests_per_second: int = 20
    recall_queries_per_tick: int = 6
    history_queries_per_tick: int = 3
    memory_writes_per_tick: int = 2
    action_slots: int = 1
    suspension_ticks: int = 240


@dataclass(slots=True)
class _Discipline:
    suspended_until_tick: int | None = None
    throttled_until_tick: int | None = None
    suspensions: int = 0
    revoked: bool = False


class LimitSet:
    def __init__(
        self,
        config: LimitConfig | None = None,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or LimitConfig()
        self.now = now or time.monotonic
        self._tick_counts: dict[tuple[str, int, Bucket], int] = defaultdict(int)
        self._slots: dict[tuple[str, int], int] = defaultdict(int)
        self._second: dict[str, deque[float]] = defaultdict(deque)
        self._strikes: dict[str, deque[tuple[int, Trigger]]] = defaultdict(deque)
        self._discipline: dict[str, _Discipline] = defaultdict(_Discipline)
        self._latest_tick = -1

    def charge(self, agent_id: str, bucket: Bucket, tick: int) -> None:
        self._observe_tick(tick)
        self._assert_available(agent_id, tick)
        discipline = self._discipline[agent_id]
        request_limit = self.config.requests_per_tick
        if discipline.throttled_until_tick is not None and tick < discipline.throttled_until_tick:
            request_limit = max(1, request_limit // 2)
        limits: Mapping[Bucket, int] = {
            "request": request_limit,
            "recall": self.config.recall_queries_per_tick,
            "history": self.config.history_queries_per_tick,
            "memory": self.config.memory_writes_per_tick,
        }
        key = (agent_id, tick, bucket)
        if self._tick_counts[key] >= limits[bucket]:
            strikes = self.strike(agent_id, tick, "rate")
            raise ProtocolError(ErrorCode.RATE_LIMITED, retry_after_ms=1_000, strikes=strikes)
        if bucket == "request":
            window = self._second[agent_id]
            now = self.now()
            while window and window[0] <= now - 1:
                window.popleft()
            if len(window) >= self.config.requests_per_second:
                strikes = self.strike(agent_id, tick, "rate")
                raise ProtocolError(ErrorCode.RATE_LIMITED, retry_after_ms=1_000, strikes=strikes)
            window.append(now)
        self._tick_counts[key] += 1

    def slot_take(self, agent_id: str, tick: int) -> int:
        self._observe_tick(tick)
        self._assert_available(agent_id, tick)
        key = (agent_id, tick)
        if self._slots[key] >= self.config.action_slots:
            raise ProtocolError(ErrorCode.NO_SLOTS)
        self._slots[key] += 1
        return self.config.action_slots - self._slots[key]

    def slot_release(self, agent_id: str, tick: int) -> None:
        key = (agent_id, tick)
        used = self._slots.get(key, 0)
        if used <= 1:
            self._slots.pop(key, None)
        else:
            self._slots[key] = used - 1

    def slots_remaining(self, agent_id: str, tick: int) -> int:
        self._observe_tick(tick)
        return max(0, self.config.action_slots - self._slots.get((agent_id, tick), 0))

    def strike(self, agent_id: str, tick: int, trigger: Trigger) -> int:
        history = self._strikes[agent_id]
        while history and history[0][0] < tick - 99:
            history.popleft()
        history.append((tick, trigger))
        count = len(history)
        discipline = self._discipline[agent_id]
        bad_signatures = sum(item_trigger == "signature" for _, item_trigger in history)
        same_tick = sum(item_tick == tick for item_tick, _ in history)
        if bad_signatures >= 5 or count >= 25:
            already_suspended = (
                discipline.suspended_until_tick is not None
                and tick < discipline.suspended_until_tick
            )
            if not already_suspended:
                discipline.suspended_until_tick = tick + self.config.suspension_ticks
                discipline.suspensions += 1
                if discipline.suspensions >= 3:
                    discipline.revoked = True
        elif count >= 10:
            discipline.throttled_until_tick = tick + 100
        if same_tick >= 3:
            self._tick_counts[(agent_id, tick, "request")] = self.config.requests_per_tick
        return count

    def status(self, agent_id: str, tick: int) -> Mapping[str, object]:
        self._observe_tick(tick)
        discipline = self._discipline[agent_id]
        return {
            "slots_remaining": self.slots_remaining(agent_id, tick),
            "strikes_100_ticks": self._windowed_strikes(agent_id, tick),
            "suspended_until_tick": discipline.suspended_until_tick,
            "throttled_until_tick": discipline.throttled_until_tick,
            "suspensions": discipline.suspensions,
            "revoked": discipline.revoked,
        }

    def _observe_tick(self, tick: int) -> None:
        if tick <= self._latest_tick:
            return
        self._latest_tick = tick
        self._tick_counts = defaultdict(
            int,
            {key: value for key, value in self._tick_counts.items() if key[1] >= tick},
        )
        self._slots = defaultdict(
            int,
            {key: value for key, value in self._slots.items() if key[1] >= tick},
        )

    def _windowed_strikes(self, agent_id: str, tick: int) -> int:
        history = self._strikes[agent_id]
        while history and history[0][0] < tick - 99:
            history.popleft()
        return len(history)

    def _assert_available(self, agent_id: str, tick: int) -> None:
        discipline = self._discipline[agent_id]
        if discipline.revoked:
            raise ProtocolError(
                ErrorCode.REVOKED,
                strikes=self._windowed_strikes(agent_id, tick),
            )
        if discipline.suspended_until_tick is not None and tick < discipline.suspended_until_tick:
            raise ProtocolError(
                ErrorCode.SUSPENDED,
                retry_after_ms=1_000,
                strikes=self._windowed_strikes(agent_id, tick),
            )
