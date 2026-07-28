from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from polis.agents.actions.resolve import Resolution, resolve_actions
from polis.agents.actions.types import Action
from polis.agents.actions.validate import ActionBudget, Validation, validate_action
from polis.agents.cognition.deliberate import Deliberation, deliberate_decide
from polis.agents.cognition.observation import Observation, build_observations
from polis.agents.cognition.reflect import Reflection, reflect_decide
from polis.agents.cognition.reflex import reflex_decide
from polis.agents.cognition.salience import RoutingResult, route_cognition
from polis.agents.education import apply_education
from polis.agents.genesis import generate_agents
from polis.agents.memory import MemoryStore
from polis.agents.state import AgentPopulation
from polis.config.runtime import RuntimeConfig
from polis.config.settings import Settings, config_hash
from polis.economy.genesis import create_economy
from polis.economy.labour import load_occupations
from polis.economy.policy import MechanicalPolicy
from polis.economy.state import EconomyState, EconomyWorldState
from polis.economy_observations import augment_economic_observations
from polis.events.kinds import (
    ACTION_VALIDATED,
    AGENT_BORN,
    AGENT_MOVED,
    COGNITION_ROUTED,
    JOURNEY_STARTED,
    LEGACY_ACTION_REJECTED,
    LIVE_AGENTS,
    LIVE_TICK,
    MEMORY_RETRIEVED,
    MEMORY_WRITTEN,
    METRIC_RECORDED,
    MOVE_BLOCKED,
    PATHS_PRECOMPUTED,
    PERCEPTION_BUILT,
    REFLECTION_PRODUCED,
    RUN_STARTED,
    SALIENCE_SCORED,
    SKILL_ACCRUED,
    WORLD_GENERATED,
)
from polis.events.log import EphemeralSink, EventLog, EventSink, MemoryEventSink
from polis.events.sampling import CognitionSampler
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock, profile_from_settings
from polis.kernel.invariants import InvariantRunner
from polis.kernel.rng import RngRegistry
from polis.kernel.scheduler import Scheduler
from polis.kernel.tick import Phase, PhaseHandler, RunReport, TickContext, TickLoop
from polis.llm.cache import CompletionCache
from polis.llm.router import LLMRouter
from polis.research.metrics import METRICS, MetricCollector
from polis.simulation import run_id_for
from polis.world.api import World
from polis.world.generator import generate_world


class DiscardEventSink:
    def __init__(self) -> None:
        self.count = 0

    async def append(self, events: Sequence[Event]) -> None:
        self.count += len(events)


@dataclass(slots=True)
class TraceRecord:
    agent_id: str
    tick: int
    perception: dict[str, object]
    salience: dict[str, object]
    retrieval: list[dict[str, object]] = field(default_factory=list)
    prompt: dict[str, object] | None = None
    response: dict[str, object] | None = None
    action: dict[str, object] | None = None
    validation: dict[str, object] | None = None
    outcome: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LivingCityResult:
    report: RunReport
    events: tuple[Event, ...]
    world: World
    population: AgentPopulation
    memory: MemoryStore
    metrics: MetricCollector
    traces: Mapping[tuple[str, int], TraceRecord]
    as_of_seq: int
    economy: EconomyState | None


@dataclass(frozen=True, slots=True)
class _Handler(PhaseHandler):
    phase: Phase
    name: str
    order: int
    callback: Callable[[TickContext], Awaitable[None]]

    async def run(self, ctx: TickContext) -> None:
        await self.callback(ctx)


def _call_payload(call: Any) -> dict[str, object]:
    payload: dict[str, object] = {
        "call_id": str(call.call_id),
        "purpose": call.purpose.value,
        "text": call.text,
        "parsed_ok": call.parsed_ok,
        "lane": call.lane,
        "model": call.model,
        "model_version": call.model_version,
        "cache_hit": call.cache_hit,
        "tokens_in": call.tokens_in,
        "tokens_out": call.tokens_out,
        "cost_usd": str(call.cost_usd),
        "latency_ms": call.latency_ms,
        "error": call.error,
    }
    if call.repair_attempts:
        payload["repair_attempts"] = call.repair_attempts
    return payload


class LivingCityEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        world: World,
        population: AgentPopulation,
        memory: MemoryStore,
        metrics: MetricCollector,
        router: LLMRouter,
        rng: RngRegistry,
        economy: EconomyState | None = None,
    ) -> None:
        self.settings = settings
        self.world = world
        self.population = population
        self.memory = memory
        self.metrics = metrics
        self.router = router
        self.rng = rng
        self.economy = economy
        self.economy_policy = (
            MechanicalPolicy(
                settings,
                population,
                world,
                economy,
                rng,
                load_occupations(settings.economy.occupations_path),
                router,
            )
            if economy is not None
            else None
        )
        self.observations: dict[str, Observation] = {}
        self.routing: RoutingResult | None = None
        self.decisions: dict[str, Action] = {}
        self.validations: dict[str, Validation] = {}
        self.resolution: Resolution | None = None
        self.traces: dict[tuple[str, int], TraceRecord] = {}

    def handlers(self) -> tuple[PhaseHandler, ...]:
        return (
            _Handler(Phase.PERCEIVE, "living.perceive", 10, self.perceive),
            _Handler(Phase.SALIENCE, "living.salience", 10, self.salience),
            _Handler(Phase.DECIDE, "living.decide", 10, self.decide),
            _Handler(Phase.VALIDATE, "living.validate", 10, self.validate),
            _Handler(Phase.RESOLVE, "living.resolve", 10, self.resolve),
            _Handler(Phase.INSTITUTIONS, "economy.institutions", 10, self.institutions),
            _Handler(Phase.VITALS, "living.vitals", 10, self.vitals),
            _Handler(Phase.METRICS, "living.metrics", 10, self.measure),
        )

    async def institutions(self, ctx: TickContext) -> None:
        if self.economy_policy is not None:
            actions = tuple(
                validation.action for _agent_id, validation in sorted(self.validations.items())
            )
            await self.economy_policy.step(ctx.tick, ctx.emit, actions)

    def _trace_kept(self, agent_id: str, tick: int, mode: str) -> bool:
        seed = self.rng.seed_for("cognition.sample", agent_id, tick)
        sampled = seed / (2**64 - 1) < self.settings.salience.cognition_sample_rate
        if self.settings.run.retention == "metrics_only":
            return sampled
        return mode in {"deliberate", "reflect"} or sampled

    async def perceive(self, ctx: TickContext) -> None:
        self.population.reset_action_counts()
        self.decisions.clear()
        self.validations.clear()
        self.resolution = None
        self.observations = build_observations(
            self.population,
            self.world,
            tick=ctx.tick,
            sim_time=ctx.sim_time,
        )
        if self.economy is not None and not self.settings.ablations.reflex_only:
            self.observations = augment_economic_observations(
                self.observations,
                self.population,
                self.economy,
            )

    async def salience(self, ctx: TickContext) -> None:
        self.routing = route_cognition(
            self.population,
            self.observations,
            self.memory,
            settings=self.settings,
            rng=self.rng,
        )
        ctx.modes.update(self.routing.modes)
        for agent_id, score in sorted(self.routing.scores.items()):
            observation = self.observations[agent_id]
            routed_mode = self.routing.modes[agent_id]
            ctx.emit(
                NewEvent(
                    PERCEPTION_BUILT,
                    {
                        "agent_id": agent_id,
                        "digest_hash": observation.digest_hash,
                        "routed_mode": routed_mode,
                    },
                    actor_id=agent_id,
                )
            )
            ctx.emit(
                NewEvent(
                    SALIENCE_SCORED,
                    {
                        "agent_id": agent_id,
                        "score": score.score,
                        "components": score.components,
                        "rank": score.rank,
                        "cutoff": self.routing.cutoff,
                        "routed_mode": routed_mode,
                    },
                    actor_id=agent_id,
                )
            )
            if self._trace_kept(agent_id, ctx.tick, routed_mode):
                self.traces[(agent_id, ctx.tick)] = TraceRecord(
                    agent_id,
                    ctx.tick,
                    observation.as_dict(),
                    {
                        "score": score.score,
                        "components": score.components,
                        "cutoff": self.routing.cutoff,
                        "rank": score.rank,
                        "routed_mode": routed_mode,
                    },
                )
        ctx.emit(
            NewEvent(
                COGNITION_ROUTED,
                {
                    "tick": ctx.tick,
                    "n_deliberate": self.routing.n_deliberate,
                    "n_reflex": self.routing.n_reflex,
                    "n_reflect": self.routing.n_reflect,
                    "cutoff": self.routing.cutoff,
                    "routed_mode": "deliberate",
                },
            )
        )

    async def decide(self, ctx: TickContext) -> None:
        if self.routing is None:
            raise RuntimeError("salience must run before decisions")
        deliberate_tasks: list[Awaitable[Deliberation]] = []
        deliberate_ids: list[str] = []
        reflect_tasks: list[Awaitable[Reflection]] = []
        reflect_ids: list[str] = []
        for agent_id, mode in sorted(self.routing.modes.items()):
            agent = self.population[agent_id]
            observation = self.observations[agent_id]
            score = self.routing.scores[agent_id]
            if mode == "reflex":
                self.decisions[agent_id] = reflex_decide(
                    agent,
                    observation,
                    self.world,
                    rng=self.rng,
                )
            elif mode == "deliberate":
                retrieval = self.memory.retrieve(
                    agent_id,
                    observation.digest_hash,
                    tick=ctx.tick,
                )
                deliberate_ids.append(agent_id)
                deliberate_tasks.append(
                    deliberate_decide(
                        agent,
                        observation,
                        retrieval,
                        router=self.router,
                        salience=score.score,
                    )
                )
            else:
                reflect_ids.append(agent_id)
                reflect_tasks.append(
                    reflect_decide(
                        agent,
                        observation,
                        memory=self.memory,
                        router=self.router,
                        world=self.world,
                        rng=self.rng,
                        salience=score.score,
                    )
                )
        deliberate_results = await asyncio.gather(*deliberate_tasks)
        reflection_results = await asyncio.gather(*reflect_tasks)
        for agent_id, deliberate in zip(deliberate_ids, deliberate_results, strict=True):
            self.decisions[agent_id] = deliberate.action
            ctx.emit(
                NewEvent(
                    MEMORY_RETRIEVED,
                    {
                        "agent_id": agent_id,
                        "memory_ids": [row.memory_id for row in deliberate.retrieval],
                        "routed_mode": "deliberate",
                    },
                    actor_id=agent_id,
                )
            )
            trace = self.traces.get((agent_id, ctx.tick))
            if trace is not None:
                trace.retrieval = [asdict(row) for row in deliberate.retrieval]
                trace.prompt = {
                    "template": "deliberate/user.v1.jinja",
                    "template_hash": sha256_text("deliberate/user.v1.jinja"),
                    "rendered": deliberate.prompt,
                    "prompt_hash": deliberate.prompt_hash,
                    "source": "stored",
                    "hash_matches": True,
                }
                trace.response = _call_payload(deliberate.call)
        for agent_id, reflection in zip(reflect_ids, reflection_results, strict=True):
            self.decisions[agent_id] = reflection.action
            for row in reflection.memories:
                ctx.emit(
                    NewEvent(
                        REFLECTION_PRODUCED,
                        {
                            "memory_id": row.memory_id,
                            "agent_id": agent_id,
                            "parent_memory_ids": row.parent_memory_ids,
                            "statement": row.text,
                            "routed_mode": "reflect",
                        },
                        actor_id=agent_id,
                    )
                )
            trace = self.traces.get((agent_id, ctx.tick))
            if trace is not None:
                trace.retrieval = [asdict(row) for row in reflection.retrieval]
                trace.response = _call_payload(reflection.call) if reflection.call else None
        ctx.actions.extend(self.decisions.values())

    async def validate(self, ctx: TickContext) -> None:
        budget = ActionBudget.for_profile(ctx.clock.profile, ctx.settings.actions)
        for agent_id, action in sorted(self.decisions.items()):
            validation = validate_action(
                action,
                agent=self.population[agent_id],
                world=self.world,
                profile=ctx.clock.profile,
                budget=budget,
            )
            self.validations[agent_id] = validation
            if validation.accepted:
                ctx.emit(
                    NewEvent(
                        ACTION_VALIDATED,
                        {
                            "action_id": str(action.action_id),
                            "agent_id": agent_id,
                            "type": action.type.value,
                            "origin": action.origin,
                            "validation": validation.gates,
                        },
                        actor_id=agent_id,
                    )
                )
            else:
                ctx.rejected.append(action)
                ctx.emit(
                    NewEvent(
                        LEGACY_ACTION_REJECTED,
                        {
                            "action_id": str(action.action_id),
                            "agent_id": agent_id,
                            "type": action.type.value,
                            "reason": validation.reason,
                            "detail": validation.detail,
                        },
                        actor_id=agent_id,
                    )
                )
            trace = self.traces.get((agent_id, ctx.tick))
            if trace is not None:
                trace.action = {
                    "action_id": str(action.action_id),
                    "type": action.type.value,
                    "params": action.params,
                    "origin": action.origin,
                    "reasoning": action.reasoning,
                }
                trace.validation = {
                    "accepted": validation.accepted,
                    "reason": validation.reason,
                    "gates": validation.gates,
                    "detail": validation.detail,
                }

    async def resolve(self, ctx: TickContext) -> None:
        resolved = tuple(
            validation.action for _agent_id, validation in sorted(self.validations.items())
        )
        self.resolution = resolve_actions(
            resolved,
            population=self.population,
            world=self.world,
            tick=ctx.tick,
            profile=ctx.clock.profile,
            rng=self.rng,
        )
        outcomes: dict[str, list[dict[str, object]]] = {}
        for movement in self.resolution.movement.movements:
            kind = AGENT_MOVED if movement.arrived else JOURNEY_STARTED
            payload: dict[str, object] = {
                "agent_id": movement.agent_id,
                "from_place": movement.from_place_id,
                "to_place": movement.to_place_id,
                "travel_ticks": movement.travel_ticks,
                "arrived": movement.arrived,
            }
            ctx.emit(
                NewEvent(
                    kind,
                    payload,
                    actor_id=movement.agent_id,
                    subject_ids=(movement.agent_id,),
                )
            )
            outcomes.setdefault(movement.agent_id, []).append(payload)
        for blocked in self.resolution.movement.blocked:
            payload = {
                "agent_id": blocked.agent_id,
                "place_id": blocked.place_id,
                "reason": blocked.reason,
            }
            ctx.emit(NewEvent(MOVE_BLOCKED, payload, actor_id=blocked.agent_id))
            outcomes.setdefault(blocked.agent_id, []).append(payload)
        for delta in apply_education(
            resolved,
            population=self.population,
            world=self.world,
            ticks_per_day=ctx.clock.profile.ticks_per_sim_day,
        ):
            ctx.emit(
                NewEvent(
                    SKILL_ACCRUED,
                    {
                        "agent_id": delta.agent_id,
                        "skill": delta.skill,
                        "delta": delta.delta,
                        "before": delta.before,
                        "after": delta.after,
                        "place_id": delta.place_id,
                    },
                    actor_id=delta.agent_id,
                )
            )
        for agent_id, validation in sorted(self.validations.items()):
            score = self.routing.scores[agent_id] if self.routing is not None else None
            row = self.memory.maybe_write_observation(
                self.population[agent_id],
                tick=ctx.tick,
                text=(
                    f"At {self.observations[agent_id].place.name}, I chose "
                    f"{validation.action.type.value}."
                ),
                salience=score.score if score is not None else 0,
            )
            if row is not None:
                ctx.emit(
                    NewEvent(
                        MEMORY_WRITTEN,
                        {
                            "memory_id": row.memory_id,
                            "agent_id": agent_id,
                            "type": row.type,
                            "importance": row.importance,
                            "text": row.text,
                            "routed_mode": self.population[agent_id].cognition_mode,
                        },
                        actor_id=agent_id,
                    )
                )
            trace = self.traces.get((agent_id, ctx.tick))
            if trace is not None:
                trace.outcome = {
                    "events": outcomes.get(agent_id, []),
                    "deltas": {
                        "needs": self.population[agent_id].needs.as_dict(),
                        "skills": self.population[agent_id].skills,
                    },
                }

    async def vitals(self, ctx: TickContext) -> None:
        for agent in self.population.alive():
            agent.decay_needs(ctx.clock.profile.ticks_per_sim_day)
            if agent.needs.energy == 0 or agent.needs.hunger == 0:
                agent.health = max(0.0, agent.health - 0.005)

    async def measure(self, ctx: TickContext) -> None:
        if self.routing is None:
            raise RuntimeError("routing is missing")
        points = self.metrics.collect(
            tick=ctx.tick,
            as_of_seq=ctx.log.last_seq,
            population=self.population,
            routing=self.routing,
            memory=self.memory,
            world=self.world,
            economy=self.economy,
            settings=self.settings,
        )
        for point in points:
            ctx.metrics[point.metric] = point.value
            ctx.emit(
                NewEvent(
                    METRIC_RECORDED,
                    {
                        "metric": point.metric,
                        "value": point.value,
                        "definition_hash": METRICS[point.metric].definition_hash,
                    },
                )
            )
        ctx.emit(
            NewEvent(
                LIVE_TICK,
                {
                    "tick": ctx.tick,
                    "sim_time": ctx.sim_time.isoformat(),
                    "events": ctx.log.last_seq,
                    "metrics": ctx.metrics,
                },
            )
        )
        tracked = sorted(
            self.routing.scores.values(),
            key=lambda row: (-row.score, row.agent_id),
        )[:250]
        ctx.emit(
            NewEvent(
                LIVE_AGENTS,
                {
                    "tick": ctx.tick,
                    "agents": [
                        {
                            "agent_id": row.agent_id,
                            "place_id": self.world.locations[row.agent_id].place_id,
                            "x": self.world.locations[row.agent_id].x,
                            "y": self.world.locations[row.agent_id].y,
                            "mode": row.routed_mode,
                            "salience": row.score,
                        }
                        for row in tracked
                    ],
                },
            )
        )


def sha256_text(value: str) -> str:
    from polis.config.canon import sha256_hex

    return sha256_hex(value.encode())


async def run_living_city(
    settings: Settings,
    *,
    ticks: int | None = None,
    sink: EventSink | None = None,
    ephemeral_sink: EphemeralSink | None = None,
    collect_events: bool = True,
    cache_mode: Literal["live", "replay", "hybrid"] | None = None,
    lane_concurrency_overrides: Mapping[str, int] | None = None,
) -> LivingCityResult:
    run_id = run_id_for(settings)
    rng = RngRegistry(settings.run.seed)
    world = generate_world(settings.world, rng)
    population = generate_agents(settings.population, world, rng)
    memory = MemoryStore(settings.memory)
    metrics = MetricCollector()
    actual_sink: EventSink
    if sink is not None:
        actual_sink = sink
    elif collect_events:
        actual_sink = MemoryEventSink()
    else:
        actual_sink = DiscardEventSink()
    sampler = CognitionSampler(
        settings.salience.cognition_sample_rate,
        lambda namespace, entity_id, tick: rng.seed_for(namespace, entity_id, tick),
    )
    log = EventLog(
        run_id,
        actual_sink,
        ephemeral_sink=ephemeral_sink,
        sampler=sampler,
    )
    clock = Clock(profile_from_settings(settings.clock))
    scheduler = Scheduler(clock)
    runtime_cache = (
        CompletionCache(
            mode=cache_mode,
            l0_entries=settings.llm.cache.l0_entries,
            verify_render=settings.llm.cache.verify_render,
            path=settings.llm.cache.path,
            namespace=str(run_id),
            schema_version=settings.llm.cache.schema_version,
            strict_version=settings.llm.cache.strict_version,
        )
        if cache_mode is not None
        else None
    )
    router = LLMRouter(
        settings=settings,
        run_id=run_id,
        lanes={} if cache_mode == "replay" else None,
        cache=runtime_cache,
        concurrency_overrides=lane_concurrency_overrides,
    )
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
    log.stage(
        NewEvent(
            WORLD_GENERATED,
            {
                "world_hash": world.world_hash,
                "width": world.width,
                "height": world.height,
                "districts": len(world.districts),
                "places": len(world.places),
            },
        ),
        tick=0,
        sim_time=clock.sim_time,
    )
    log.stage(
        NewEvent(
            PATHS_PRECOMPUTED,
            {
                "world_hash": world.world_hash,
                "pairs": len(world.places) ** 2,
                "method": "deterministic_grid_cost",
            },
        ),
        tick=0,
        sim_time=clock.sim_time,
    )
    for agent in population:
        log.stage(
            NewEvent(
                AGENT_BORN,
                {
                    "agent_id": agent.agent_id,
                    "display_name": agent.display_name,
                    "age_years": agent.age_years,
                    "home_place_id": agent.home_place_id,
                    "traits": agent.traits.as_dict(),
                    "education_level": agent.education_level,
                },
                actor_id=agent.agent_id,
                subject_ids=(agent.agent_id,),
            ),
            tick=0,
            sim_time=clock.sim_time,
        )
    economy: EconomyState | None = None
    if settings.economy.enabled:
        economy = create_economy(
            settings,
            population,
            world,
            rng,
            run_id,
            emit=lambda draft: log.stage(
                draft,
                tick=0,
                sim_time=clock.sim_time,
            ),
        ).state
    await log.commit(0)
    engine = LivingCityEngine(
        settings=settings,
        world=world,
        population=population,
        memory=memory,
        metrics=metrics,
        router=router,
        rng=rng,
        economy=economy,
    )
    runtime = RuntimeConfig(settings)
    loop = TickLoop(
        run_id=run_id,
        clock=clock,
        rng=rng,
        scheduler=scheduler,
        log=log,
        runtime=runtime,
        settings=settings,
        invariants=InvariantRunner(clock),
        state=(
            EconomyWorldState(
                population,
                economy,
                ticks_per_year=(
                    settings.clock.days_per_sim_year * settings.clock.ticks_per_sim_day
                ),
            )
            if economy is not None
            else population
        ),
    )
    for handler in engine.handlers():
        loop.register(handler)
    await router.start()
    try:
        report = await loop.run(ticks if ticks is not None else settings.run.ticks)
    finally:
        await router.close()
    events = tuple(actual_sink.events) if isinstance(actual_sink, MemoryEventSink) else ()
    return LivingCityResult(
        report,
        events,
        world,
        population,
        memory,
        metrics,
        engine.traces,
        log.last_seq,
        economy,
    )
