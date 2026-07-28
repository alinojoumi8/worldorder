from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from polis.events.hashing import seal
from polis.events.kinds import Persistence, is_ephemeral, spec
from polis.events.sampling import CognitionSampler
from polis.events.schemas import validate_payload
from polis.events.types import EPHEMERAL_SEQ, GENESIS_PREV_HASH, Event, NewEvent


class EventSink(Protocol):
    async def append(self, events: Sequence[Event]) -> None: ...


class EphemeralSink(Protocol):
    async def publish(self, events: Sequence[Event]) -> None: ...


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def append(self, events: Sequence[Event]) -> None:
        self.events.extend(events)


class NullEphemeralSink:
    async def publish(self, events: Sequence[Event]) -> None:
        return None


@dataclass(frozen=True, slots=True)
class CommitResult:
    tick: int
    persisted: int
    ephemeral: int
    dropped_sampled: int
    first_seq: int
    last_seq: int
    chain_hash: str


@dataclass(frozen=True, slots=True)
class EventSavepoint:
    staged_len: int
    last_seq: int
    chain_hash: str
    dropped_sampled: int


class EventLog:
    def __init__(
        self,
        run_id: UUID,
        sink: EventSink,
        *,
        ephemeral_sink: EphemeralSink | None = None,
        validate: bool = True,
        start_seq: int = 0,
        start_prev_hash: str = GENESIS_PREV_HASH,
        sampler: CognitionSampler | None = None,
    ) -> None:
        self.run_id = run_id
        self.sink = sink
        self.ephemeral_sink = ephemeral_sink or NullEphemeralSink()
        self.validate = validate
        self.sampler = sampler
        self._last_seq = start_seq
        self._chain_hash = start_prev_hash
        self._staged: list[Event] = []
        self._dropped_sampled = 0
        self._rollback_seq = start_seq
        self._rollback_hash = start_prev_hash

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def chain_hash(self) -> str:
        return self._chain_hash

    def stage(self, draft: NewEvent, *, tick: int, sim_time: datetime) -> Event:
        if self.validate:
            validate_payload(draft.kind, draft.payload)
        if is_ephemeral(draft.kind):
            event = Event(
                EPHEMERAL_SEQ,
                self.run_id,
                tick,
                sim_time,
                draft.kind,
                draft.actor_id,
                tuple(sorted(draft.subject_ids)),
                draft.cause_seq,
                draft.payload,
                draft.sig,
                "",
                "",
            )
            self._staged.append(event)
            return event

        persistence = spec(draft.kind).persistence
        if persistence == Persistence.SAMPLED and self.sampler is not None:
            routed_mode = str(draft.payload.get("routed_mode", "reflex"))
            candidate = Event(
                self._last_seq + 1,
                self.run_id,
                tick,
                sim_time,
                draft.kind,
                draft.actor_id,
                tuple(sorted(draft.subject_ids)),
                draft.cause_seq,
                draft.payload,
                draft.sig,
                self._chain_hash,
                "",
            )
            if not self.sampler.keep(candidate, routed_mode=routed_mode):
                self._dropped_sampled += 1
                return candidate

        self._last_seq += 1
        event = seal(
            draft,
            seq=self._last_seq,
            run_id=self.run_id,
            tick=tick,
            sim_time=sim_time,
            prev_hash=self._chain_hash,
        )
        self._chain_hash = event.hash
        self._staged.append(event)
        return event

    def staged(self) -> tuple[Event, ...]:
        return tuple(self._staged)

    def savepoint(self) -> EventSavepoint:
        return EventSavepoint(
            len(self._staged),
            self._last_seq,
            self._chain_hash,
            self._dropped_sampled,
        )

    def rollback_to(self, savepoint: EventSavepoint) -> None:
        if not 0 <= savepoint.staged_len <= len(self._staged):
            raise ValueError("event savepoint is not valid for the current staged batch")
        del self._staged[savepoint.staged_len :]
        self._last_seq = savepoint.last_seq
        self._chain_hash = savepoint.chain_hash
        self._dropped_sampled = savepoint.dropped_sampled

    async def commit(self, tick: int) -> CommitResult:
        persisted = [event for event in self._staged if event.seq != EPHEMERAL_SEQ]
        ephemeral = [event for event in self._staged if event.seq == EPHEMERAL_SEQ]
        try:
            await self.sink.append(persisted)
            await self.ephemeral_sink.publish(ephemeral)
        except Exception:
            self.rollback()
            raise
        first_seq = persisted[0].seq if persisted else self._last_seq
        result = CommitResult(
            tick=tick,
            persisted=len(persisted),
            ephemeral=len(ephemeral),
            dropped_sampled=self._dropped_sampled,
            first_seq=first_seq,
            last_seq=self._last_seq,
            chain_hash=self._chain_hash,
        )
        self._staged.clear()
        self._dropped_sampled = 0
        self._rollback_seq = self._last_seq
        self._rollback_hash = self._chain_hash
        return result

    def rollback(self) -> None:
        self._last_seq = self._rollback_seq
        self._chain_hash = self._rollback_hash
        self._staged.clear()
        self._dropped_sampled = 0
