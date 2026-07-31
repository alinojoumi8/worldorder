from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from uuid import UUID

from polis.config.runtime import RuntimeConfig
from polis.config.settings import Settings, config_hash
from polis.events.kinds import RUN_STARTED, TICK_COMPLETED
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock, profile_from_settings
from polis.kernel.det import det_uuid
from polis.kernel.invariants import InvariantRunner, NullWorldState
from polis.kernel.rng import RngRegistry
from polis.kernel.scheduler import Scheduler
from polis.kernel.tick import RunReport, TickLoop
from polis.llm.cache import EMPTY_COMPLETION_CACHE_MANIFEST_HASH
from polis.run_identity import RunIdentity, build_run_identity, validate_run_identity


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
    run_identity: RunIdentity | None = None,
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
    genesis_completion = next(
        (event for event in resume_events if event.kind == TICK_COMPLETED and event.tick == 0),
        None,
    )
    genesis_completed = genesis_completion is not None
    if last is None:
        if run_identity is None:
            identity = build_run_identity(settings)
        else:
            validate_run_identity(
                settings,
                run_identity,
                completion_cache_manifest_hash=EMPTY_COMPLETION_CACHE_MANIFEST_HASH,
            )
            identity = run_identity
        log.stage(
            NewEvent(
                RUN_STARTED,
                identity.event_payload(),
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
    if genesis_completion is not None and genesis_completion.payload.get("halted") is True:
        raise ValueError("cannot resume a run halted during genesis")
    if not genesis_completed:
        if last is not None and last.tick > 0:
            raise ValueError("resume event stream is missing tick-zero completion")
        genesis = await loop.complete_genesis_tick()
        if genesis.halted:
            report = RunReport(
                run_id=run_id,
                first_tick=0,
                last_tick=0,
                ticks=1,
                events=log.last_seq,
                chain_hash=genesis.chain_hash,
                status="halted",
                halt_reason=genesis.halt_reason,
            )
            return SimulationResult(report, tuple(sink.events))
    report = await loop.run(ticks if ticks is not None else settings.run.ticks)
    return SimulationResult(report, tuple(sink.events))
