from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import IntEnum
from typing import Any, Final, Protocol, cast
from uuid import UUID

from polis.config.runtime import RuntimeConfig
from polis.config.settings import Settings
from polis.events.kinds import (
    INVARIANT_VIOLATED,
    TICK_COMPLETED,
    TICK_STARTED,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.invariants import InvariantRunner, Violation, WorldStateView
from polis.kernel.rng import RngRegistry
from polis.kernel.scheduler import Scheduler


class Phase(IntEnum):
    CLOCK = 0
    PERCEIVE = 1
    SALIENCE = 2
    DECIDE = 3
    VALIDATE = 4
    RESOLVE = 5
    COMMIT = 6
    INSTITUTIONS = 7
    VITALS = 8
    METRICS = 9


PHASE_BUDGET_MS: Final[Mapping[Phase, int]] = {
    Phase.CLOCK: 1,
    Phase.PERCEIVE: 80,
    Phase.SALIENCE: 20,
    Phase.DECIDE: 3000,
    Phase.VALIDATE: 20,
    Phase.RESOLVE: 100,
    Phase.COMMIT: 150,
    Phase.INSTITUTIONS: 100,
    Phase.VITALS: 30,
    Phase.METRICS: 50,
}


@dataclass(slots=True)
class TickContext:
    run_id: UUID
    tick: int
    sim_time: datetime
    clock: Clock
    rng: RngRegistry
    scheduler: Scheduler
    log: EventLog
    runtime: RuntimeConfig
    settings: Settings
    due: tuple[str, ...] = ()
    observations: dict[str, Any] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)
    actions: list[Any] = field(default_factory=list)
    rejected: list[Any] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    timings: dict[Phase, float] = field(default_factory=dict)
    traces: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    halt_reason: str | None = None

    def emit(self, draft: NewEvent) -> Event:
        return self.log.stage(draft, tick=self.tick, sim_time=self.sim_time)


class PhaseHandler(Protocol):
    phase: Phase
    name: str
    order: int

    async def run(self, ctx: TickContext) -> None: ...


@dataclass(frozen=True, slots=True)
class TickReport:
    tick: int
    sim_time: datetime
    events: int
    ephemerals: int
    actions: int
    rejected: int
    llm_calls: int
    cost_usd: Decimal
    timings_ms: Mapping[str, float]
    over_budget: tuple[str, ...]
    violations: tuple[Violation, ...]
    chain_hash: str
    halted: bool
    halt_reason: str | None


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: UUID
    first_tick: int
    last_tick: int
    ticks: int
    events: int
    chain_hash: str
    status: str
    halt_reason: str | None


class TickLoop:
    def __init__(
        self,
        *,
        run_id: UUID,
        clock: Clock,
        rng: RngRegistry,
        scheduler: Scheduler,
        log: EventLog,
        runtime: RuntimeConfig,
        settings: Settings,
        invariants: InvariantRunner,
        state: WorldStateView,
    ) -> None:
        self.run_id = run_id
        self.clock = clock
        self.rng = rng
        self.scheduler = scheduler
        self.log = log
        self.runtime = runtime
        self.settings = settings
        self.invariants = invariants
        self.state = state
        self._handlers: list[PhaseHandler] = []

    def register(self, handler: PhaseHandler) -> None:
        self._handlers.append(handler)
        self._handlers.sort(key=lambda item: (item.phase, item.order, item.name))

    async def run_tick(self) -> TickReport:
        tick = self.clock.advance()
        if hasattr(self.state, "tick"):
            cast(Any, self.state).tick = tick
        ctx = TickContext(
            self.run_id,
            tick,
            self.clock.sim_time,
            self.clock,
            self.rng,
            self.scheduler,
            self.log,
            self.runtime,
            self.settings,
            due=self.scheduler.due(tick),
        )
        ctx.emit(NewEvent(TICK_STARTED, {"tick": tick, "due": ctx.due}))
        first_commit_events = 0
        first_commit_ephemerals = 0
        for phase in Phase:
            started = asyncio.get_running_loop().time()
            for handler in self._handlers:
                if handler.phase == phase:
                    await handler.run(ctx)
            if phase == Phase.DECIDE:
                ctx.actions.sort(
                    key=lambda action: (
                        str(getattr(action, "actor_id", "")),
                        str(getattr(action, "action_id", "")),
                    )
                )
            if phase == Phase.COMMIT:
                result = await self.log.commit(tick)
                first_commit_events = result.persisted
                first_commit_ephemerals = result.ephemeral
            ctx.timings[phase] = (asyncio.get_running_loop().time() - started) * 1000

        results = self.invariants.run(tick, self.state)
        violations = tuple(result for result in results if isinstance(result, Violation))
        for violation in violations:
            ctx.emit(
                NewEvent(
                    INVARIANT_VIOLATED,
                    {
                        "invariant_id": violation.invariant_id,
                        "expected": violation.expected,
                        "actual": violation.actual,
                        "detail": violation.detail,
                        "halting": violation.severity == "halt",
                    },
                )
            )
        halted = self.invariants.should_halt(results)
        ctx.emit(
            NewEvent(
                TICK_COMPLETED,
                {
                    "tick": tick,
                    "event_count": first_commit_events + len(self.log.staged()) + 1,
                    "actions": len(ctx.actions),
                    "rejected": len(ctx.rejected),
                    "halted": halted,
                },
            )
        )
        final = await self.log.commit(tick)
        over_budget = tuple(
            phase.name for phase, elapsed in ctx.timings.items() if elapsed > PHASE_BUDGET_MS[phase]
        )
        return TickReport(
            tick=tick,
            sim_time=ctx.sim_time,
            events=first_commit_events + final.persisted,
            ephemerals=first_commit_ephemerals + final.ephemeral,
            actions=len(ctx.actions),
            rejected=len(ctx.rejected),
            llm_calls=sum(1 for mode in ctx.modes.values() if mode != "reflex"),
            cost_usd=Decimal(0),
            timings_ms={phase.name: value for phase, value in ctx.timings.items()},
            over_budget=over_budget,
            violations=violations,
            chain_hash=final.chain_hash,
            halted=halted,
            halt_reason=violations[0].invariant_id if halted else None,
        )

    async def run(
        self,
        until_tick: int,
        *,
        on_tick: Callable[[TickReport], None] | None = None,
    ) -> RunReport:
        first = self.clock.tick + 1
        reports: list[TickReport] = []
        while self.clock.tick < until_tick:
            report = await self.run_tick()
            reports.append(report)
            if on_tick is not None:
                on_tick(report)
            if report.halted:
                break
        last = reports[-1] if reports else None
        return RunReport(
            run_id=self.run_id,
            first_tick=first,
            last_tick=last.tick if last else self.clock.tick,
            ticks=len(reports),
            events=sum(report.events for report in reports),
            chain_hash=self.log.chain_hash,
            status="halted" if last and last.halted else "completed",
            halt_reason=last.halt_reason if last else None,
        )
