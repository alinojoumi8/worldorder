from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from polis.events.log import MemoryEventSink
from polis.events.types import Event


@dataclass(frozen=True, slots=True)
class EventQuery:
    run_id: UUID
    kinds: frozenset[int] | None = None
    kind_range: tuple[int, int] | None = None
    actor_id: str | None = None
    subject_id: str | None = None
    from_tick: int | None = None
    to_tick: int | None = None
    from_seq: int | None = None
    to_seq: int | None = None
    order: Literal["seq", "seq_desc"] = "seq"
    limit: int | None = None


class EventReader(Protocol):
    async def get(self, run_id: UUID, seq: int) -> Event | None: ...

    def scan(self, query: EventQuery) -> AsyncIterator[Event]: ...

    async def count(self, query: EventQuery) -> int: ...

    async def last(self, run_id: UUID) -> Event | None: ...

    async def by_cause(self, run_id: UUID, cause_seq: int) -> list[Event]: ...


class MemoryEventReader:
    def __init__(self, sink: MemoryEventSink) -> None:
        self.sink = sink

    def _filtered(self, query: EventQuery) -> list[Event]:
        values = []
        for event in self.sink.events:
            if event.run_id != query.run_id:
                continue
            if query.kinds is not None and event.kind not in query.kinds:
                continue
            if query.kind_range and not (query.kind_range[0] <= event.kind <= query.kind_range[1]):
                continue
            if query.actor_id is not None and event.actor_id != query.actor_id:
                continue
            if query.subject_id is not None and query.subject_id not in event.subject_ids:
                continue
            if query.from_tick is not None and event.tick < query.from_tick:
                continue
            if query.to_tick is not None and event.tick > query.to_tick:
                continue
            if query.from_seq is not None and event.seq < query.from_seq:
                continue
            if query.to_seq is not None and event.seq > query.to_seq:
                continue
            values.append(event)
        values.sort(key=lambda event: event.seq, reverse=query.order == "seq_desc")
        return values[: query.limit] if query.limit is not None else values

    async def get(self, run_id: UUID, seq: int) -> Event | None:
        return next(
            (event for event in self.sink.events if event.run_id == run_id and event.seq == seq),
            None,
        )

    async def scan(self, query: EventQuery) -> AsyncIterator[Event]:
        for event in self._filtered(query):
            yield event

    async def count(self, query: EventQuery) -> int:
        return len(self._filtered(query))

    async def last(self, run_id: UUID) -> Event | None:
        events = [event for event in self.sink.events if event.run_id == run_id]
        return max(events, key=lambda event: event.seq, default=None)

    async def by_cause(self, run_id: UUID, cause_seq: int) -> list[Event]:
        return sorted(
            [
                event
                for event in self.sink.events
                if event.run_id == run_id and event.cause_seq == cause_seq
            ],
            key=lambda event: event.seq,
        )
