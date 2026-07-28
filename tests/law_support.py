from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from polis.config.runtime import RuntimeConfig
from polis.config.settings import LawSettings, SocietySettings, WorldSettings, load_settings
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import Event
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.media.checker import ClaimChecker, MemoryCheckContext
from polis.world.generator import generate_world


class Memories:
    def __init__(self, rows: Iterable[tuple[str, int]] = ()) -> None:
        self.rows = set(rows)

    def holds_memory_of(self, agent_id: str, event_seq: int) -> bool:
        return (agent_id, event_seq) in self.rows

    def holders_of(self, event_seq: int) -> frozenset[str]:
        return frozenset(agent_id for agent_id, seq in self.rows if seq == event_seq)

    def retrieve_recent_texts(self, agent_id: str, tick: int, n: int) -> tuple[str, ...]:
        del agent_id, tick, n
        return ()


class Offices:
    def holder(self, office: str, tick: int) -> str | None:
        del tick
        return {"judge": "ag_judge", "police_chief": "ag_chief"}.get(office)

    def holds_office(self, agent_id: str, tick: int) -> str | None:
        del tick
        return {
            "ag_judge": "judge",
            "ag_chief": "police_chief",
        }.get(agent_id)


class RecordingLawLedger:
    def __init__(self, balances: dict[str, int] | None = None) -> None:
        self.balances = dict(balances or {})
        self.transfers: list[tuple[str, str, int, str]] = []
        self.ordinal = 0

    def compatible_balance(self, payer_id: str, payee_id: str) -> int:
        del payee_id
        return self.balances.get(payer_id, 0)

    def can_pay(self, payer_id: str, cents: int, payee_id: str | None = None) -> bool:
        del payee_id
        return self.balances.get(payer_id, 0) >= cents

    def next_transfer_id(self, tick: int) -> str:
        return f"tx_{tick}_{self.ordinal + 1}"

    def post_transfer(
        self,
        payer_id: str,
        payee_id: str,
        cents: int,
        *,
        reason: str,
        tick: int,
        cause: Event,
    ) -> str:
        del cause
        amount = min(cents, self.balances.get(payer_id, 0))
        self.balances[payer_id] = self.balances.get(payer_id, 0) - amount
        self.balances[payee_id] = self.balances.get(payee_id, 0) + amount
        self.transfers.append((payer_id, payee_id, amount, reason))
        self.ordinal += 1
        return f"tx_{tick}_{self.ordinal}"


def clock() -> Clock:
    return Clock(PROFILES["microscope"])


def log(seed: int = 19) -> EventLog:
    return EventLog(UUID(int=seed), MemoryEventSink())


def runtime() -> RuntimeConfig:
    return RuntimeConfig(load_settings(Path("configs/smoke.yaml")))


def law_cfg(**changes: object) -> LawSettings:
    return LawSettings().model_copy(update=changes)


def world():
    return generate_world(
        WorldSettings(width=40, height=40, districts=4, places_per_district=12),
        RngRegistry(19),
    )


def checker(
    event_log: EventLog,
    *,
    facts: Iterable[tuple[str, str, int, object, tuple[int, ...]]] = (),
) -> ClaimChecker:
    context = MemoryCheckContext()
    for predicate, entity_id, tick, value, event_seqs in facts:
        context.record(predicate, entity_id, tick, value, event_seqs)
    return ClaimChecker(
        ctx=context,
        log=event_log,
        cfg=SocietySettings(),
        clock=clock(),
    )


def event(
    seq: int,
    tick: int,
    kind: int,
    *,
    subjects: tuple[str, ...] = (),
    payload: dict[str, object] | None = None,
) -> Event:
    return Event(
        seq=seq,
        run_id=UUID(int=19),
        tick=tick,
        sim_time=clock().sim_time_at(tick),
        kind=kind,
        actor_id=None,
        subject_ids=subjects,
        cause_seq=None,
        payload=payload or {},
        sig=None,
        prev_hash="0" * 64,
        hash=f"{seq:064x}",
    )
