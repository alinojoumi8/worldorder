from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from uuid import UUID

from polis.config.runtime import RuntimeConfig
from polis.config.settings import Settings, config_hash
from polis.events.kinds import RUN_STARTED
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock, profile_from_settings
from polis.kernel.det import det_uuid
from polis.kernel.invariants import InvariantRunner, NullWorldState
from polis.kernel.rng import RngRegistry
from polis.kernel.scheduler import Scheduler
from polis.kernel.tick import RunReport, TickLoop


@dataclass(frozen=True, slots=True)
class SimulationResult:
    report: RunReport
    events: tuple[object, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self.report)


def run_id_for(settings: Settings) -> UUID:
    return det_uuid("polis.run", config_hash(settings), settings.run.seed)


async def run_empty(
    settings: Settings,
    *,
    ticks: int | None = None,
    resume_events: Sequence[Event] = (),
) -> SimulationResult:
    run_id = run_id_for(settings)
    sink = MemoryEventSink()
    sink.events.extend(resume_events)
    last = resume_events[-1] if resume_events else None
    if last is not None and last.run_id != run_id:
        raise ValueError("resume event stream belongs to a different run")
    log = EventLog(
        run_id,
        sink,
        start_seq=last.seq if last is not None else 0,
        start_prev_hash=last.hash if last is not None else "0" * 64,
    )
    clock = Clock(
        profile_from_settings(settings.clock),
        start_tick=last.tick if last is not None else 0,
    )
    rng = RngRegistry(settings.run.seed)
    scheduler = Scheduler(clock)
    runtime = RuntimeConfig(settings)
    state = NullWorldState()
    invariants = InvariantRunner(clock)
    if last is None:
        log.stage(
            NewEvent(
                RUN_STARTED,
                {
                    "config_hash": config_hash(settings),
                    "seed": settings.run.seed,
                    "scale": settings.population.initial_agents,
                },
            ),
            tick=0,
            sim_time=clock.sim_time,
        )
        await log.commit(0)
    loop = TickLoop(
        run_id=run_id,
        clock=clock,
        rng=rng,
        scheduler=scheduler,
        log=log,
        runtime=runtime,
        settings=settings,
        invariants=invariants,
        state=state,
    )
    report = await loop.run(ticks if ticks is not None else settings.run.ticks)
    return SimulationResult(report, tuple(sink.events))
