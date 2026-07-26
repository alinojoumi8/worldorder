from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from polis.events.hashing import recompute
from polis.events.reader import EventQuery, EventReader
from polis.events.schemas import PayloadSchemaError, validate_payload
from polis.events.types import GENESIS_PREV_HASH, Event

Reason = Literal[
    "hash_mismatch",
    "prev_hash_mismatch",
    "seq_gap",
    "schema_invalid",
    "tick_regression",
]


@dataclass(frozen=True, slots=True)
class ChainFailure:
    seq: int
    kind: int
    reason: Reason
    expected: str
    actual: str


@dataclass(frozen=True, slots=True)
class ChainReport:
    run_id: UUID
    events_checked: int
    first_seq: int
    last_seq: int
    terminal_hash: str
    ok: bool
    signatures_verified: int
    unknown_kinds: tuple[int, ...]
    failures: tuple[ChainFailure, ...]


def verify_batch(
    events: Sequence[Event],
    *,
    start_prev_hash: str = GENESIS_PREV_HASH,
    start_seq: int = 0,
) -> ChainReport:
    failures: list[ChainFailure] = []
    previous_hash = start_prev_hash
    previous_seq = start_seq
    previous_tick = -1
    for event in events:
        if event.seq != previous_seq + 1:
            failures.append(
                ChainFailure(
                    event.seq, event.kind, "seq_gap", str(previous_seq + 1), str(event.seq)
                )
            )
        if event.prev_hash != previous_hash:
            failures.append(
                ChainFailure(
                    event.seq,
                    event.kind,
                    "prev_hash_mismatch",
                    previous_hash,
                    event.prev_hash,
                )
            )
        expected = recompute(event)
        if expected != event.hash:
            failures.append(
                ChainFailure(event.seq, event.kind, "hash_mismatch", expected, event.hash)
            )
        if event.tick < previous_tick:
            failures.append(
                ChainFailure(
                    event.seq,
                    event.kind,
                    "tick_regression",
                    str(previous_tick),
                    str(event.tick),
                )
            )
        try:
            validate_payload(event.kind, event.payload)
        except PayloadSchemaError as exc:
            failures.append(
                ChainFailure(event.seq, event.kind, "schema_invalid", "valid", str(exc))
            )
        previous_hash = event.hash
        previous_seq = event.seq
        previous_tick = event.tick
    run_id = events[0].run_id if events else UUID(int=0)
    return ChainReport(
        run_id=run_id,
        events_checked=len(events),
        first_seq=events[0].seq if events else start_seq,
        last_seq=events[-1].seq if events else start_seq,
        terminal_hash=previous_hash,
        ok=not failures,
        signatures_verified=0,
        unknown_kinds=(),
        failures=tuple(failures),
    )


async def verify_run(
    reader: EventReader,
    run_id: UUID,
    *,
    check_signatures: bool = True,
    check_schemas: bool = True,
    from_seq: int = 0,
    stop_on_first: bool = False,
    progress: Callable[[int], None] | None = None,
) -> ChainReport:
    del check_signatures, check_schemas, stop_on_first
    events = [event async for event in reader.scan(EventQuery(run_id, from_seq=from_seq or None))]
    if progress is not None:
        progress(len(events))
    return verify_batch(events, start_seq=max(0, from_seq - 1))
