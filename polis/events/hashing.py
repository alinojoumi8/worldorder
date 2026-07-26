from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from polis.config.canon import canonical_bytes
from polis.events.types import Event, NewEvent


def canonical_event_bytes(
    *,
    seq: int,
    run_id: UUID,
    tick: int,
    sim_time: datetime,
    kind: int,
    actor_id: str | None,
    subject_ids: Sequence[str],
    cause_seq: int | None,
    payload: Mapping[str, Any],
    sig: str | None,
    prev_hash: str,
) -> bytes:
    if sim_time.tzinfo is not None or sim_time.microsecond:
        raise ValueError("sim_time must be UTC-naive with microsecond == 0")
    return b"".join(
        (
            seq.to_bytes(8, "big"),
            run_id.bytes,
            tick.to_bytes(8, "big"),
            sim_time.isoformat().encode(),
            kind.to_bytes(4, "big"),
            (actor_id or "").encode(),
            "\x1f".join(sorted(subject_ids)).encode(),
            (cause_seq if cause_seq is not None else -1).to_bytes(8, "big", signed=True),
            canonical_bytes(payload),
            (sig or "").encode(),
            bytes.fromhex(prev_hash),
        )
    )


def event_hash(**kwargs: Any) -> str:
    return hashlib.sha256(canonical_event_bytes(**kwargs)).hexdigest()


def seal(
    draft: NewEvent,
    *,
    seq: int,
    run_id: UUID,
    tick: int,
    sim_time: datetime,
    prev_hash: str,
) -> Event:
    digest = event_hash(
        seq=seq,
        run_id=run_id,
        tick=tick,
        sim_time=sim_time,
        kind=draft.kind,
        actor_id=draft.actor_id,
        subject_ids=draft.subject_ids,
        cause_seq=draft.cause_seq,
        payload=draft.payload,
        sig=draft.sig,
        prev_hash=prev_hash,
    )
    return Event(
        seq=seq,
        run_id=run_id,
        tick=tick,
        sim_time=sim_time,
        kind=draft.kind,
        actor_id=draft.actor_id,
        subject_ids=tuple(sorted(draft.subject_ids)),
        cause_seq=draft.cause_seq,
        payload=draft.payload,
        sig=draft.sig,
        prev_hash=prev_hash,
        hash=digest,
    )


def recompute(event: Event) -> str:
    return event_hash(
        seq=event.seq,
        run_id=event.run_id,
        tick=event.tick,
        sim_time=event.sim_time,
        kind=event.kind,
        actor_id=event.actor_id,
        subject_ids=event.subject_ids,
        cause_seq=event.cause_seq,
        payload=event.payload,
        sig=event.sig,
        prev_hash=event.prev_hash,
    )


def verify_event(event: Event) -> bool:
    return recompute(event) == event.hash
