from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

GENESIS_PREV_HASH: Final = "0" * 64
EPHEMERAL_SEQ: Final = -1


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    run_id: UUID
    tick: int
    sim_time: datetime
    kind: int
    actor_id: str | None
    subject_ids: tuple[str, ...]
    cause_seq: int | None
    payload: Mapping[str, Any]
    sig: str | None
    prev_hash: str
    hash: str


@dataclass(frozen=True, slots=True)
class NewEvent:
    kind: int
    payload: Mapping[str, Any]
    actor_id: str | None = None
    subject_ids: tuple[str, ...] = ()
    cause_seq: int | None = None
    sig: str | None = None
