from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from polis.agents.actions.params import PARAMS_MODELS
from polis.agents.actions.protocol import ResolutionContext, ValidationContext
from polis.agents.actions.resolve import Resolution, resolve_actions
from polis.agents.actions.types import Action, ActionType, LegalityVerdict, ValidatedAction
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
from polis.config.canon import canonical_bytes, sha256_hex
from polis.config.runtime import RuntimeConfig
from polis.config.settings import Settings
from polis.demography_runtime import DemographyRuntime, build_demography_runtime
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
    EXTERNAL_ACTION_REJECTED,
    EXTERNAL_ACTION_SUBMITTED,
    EXTERNAL_AGENT_NATURALISED,
    EXTERNAL_AGENT_REGISTERED,
    EXTERNAL_ARENA_INVALIDATED,
    EXTERNAL_CONTROL_RESUMED,
    EXTERNAL_DEADLINE_MISSED,
    EXTERNAL_GATEWAY_DEGRADED,
    EXTERNAL_INJECTION_FLAGGED,
    EXTERNAL_KEY_REVOKED,
    EXTERNAL_OBSERVATION_PUSHED,
    EXTERNAL_REGISTRATION_REJECTED,
    EXTERNAL_REGISTRATION_REQUESTED,
    EXTERNAL_SESSION_CLOSED,
    EXTERNAL_SESSION_OPENED,
    EXTERNAL_SIM_AWARE_FLAGGED,
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
from polis.external import ExternalAction, ExternalDecisionPort
from polis.kernel.clock import Clock, profile_from_settings
from polis.kernel.invariants import InvariantRunner
from polis.kernel.rng import RngRegistry
from polis.kernel.scheduler import Scheduler
from polis.kernel.tick import Phase, PhaseHandler, RunReport, TickContext, TickLoop
from polis.llm.cache import EMPTY_COMPLETION_CACHE_MANIFEST_HASH, CompletionCache
from polis.llm.router import LLMRouter
from polis.research.metrics import METRICS, MetricCollector
from polis.run_identity import RunIdentity, build_run_identity, validate_run_identity
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
    demography: DemographyRuntime | None
    completion_cache_manifest: Mapping[str, str]
    completion_cache_manifest_hash: str


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
        log: EventLog | None = None,
        clock: Clock | None = None,
        runtime: RuntimeConfig | None = None,
        external_decisions: ExternalDecisionPort | None = None,
        final_tick: int | None = None,
    ) -> None:
        self.settings = settings
        self.world = world
        self.population = population
        self.memory = memory
        self.metrics = metrics
        self.router = router
        self.rng = rng
        self.runtime = runtime
        self.external_decisions = external_decisions
        self._external_misses: dict[str, int] = {}
        self._external_deadlines_missed: dict[str, int] = {}
        self._external_ticks_driven: dict[str, int] = {}
        self._final_tick = final_tick if final_tick is not None else settings.run.ticks
        self._external_arena_checked = False
        self._naturalised_external: set[str] = set()
        self._naturalised_at: dict[str, int] = {}
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
        self.demography = (
            build_demography_runtime(
                settings=settings,
                log=log,
                clock=clock,
                rng=rng,
                world=world,
                population=population,
                memory=memory,
                runtime=runtime,
                economy_policy=self.economy_policy,
            )
            if self.economy_policy is not None
            and log is not None
            and clock is not None
            and runtime is not None
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
            _Handler(Phase.VITALS, "demography.vitals", 10, self.demography_vitals),
            _Handler(Phase.VITALS, "living.vitals", 20, self.vitals),
            _Handler(Phase.METRICS, "living.metrics", 10, self.measure),
        )

    async def institutions(self, ctx: TickContext) -> None:
        if self.economy_policy is not None:
            actions = tuple(
                validation.action for _agent_id, validation in sorted(self.validations.items())
            )
            await self.economy_policy.step(ctx.tick, ctx.emit, actions)
        await self._apply_external_lifecycle(ctx)

    async def _apply_external_lifecycle(self, ctx: TickContext) -> None:
        if self.external_decisions is None:
            return
        try:
            requests = await asyncio.wait_for(
                self.external_decisions.drain_lifecycle(
                    ctx.tick,
                    timeout_ms=self.settings.gateway.deadline.drain_timeout_ms,
                ),
                timeout=self.settings.gateway.deadline.drain_timeout_ms / 1_000 + 0.25,
            )
        except (TimeoutError, OSError):
            ctx.emit(
                NewEvent(
                    EXTERNAL_GATEWAY_DEGRADED,
                    {
                        "reason": "drain_timeout",
                        "affected_agent_ids": [],
                        "tick": ctx.tick,
                    },
                )
            )
            return
        if not isinstance(requests, Sequence):
            ctx.emit(
                NewEvent(
                    EXTERNAL_GATEWAY_DEGRADED,
                    {
                        "reason": "malformed_lifecycle_batch",
                        "affected_agent_ids": sorted(self._controlled_external_ids()),
                        "tick": ctx.tick,
                    },
                )
            )
            return
        for request in requests:
            if request.request_type == "malformed":
                self._emit_malformed_lifecycle(ctx, request.agent_id, "unknown")
                continue
            if request.request_type == "register":
                declaration = request.declaration
                try:
                    if not isinstance(declaration, Mapping):
                        raise TypeError("registration declaration must be a mapping")
                    pubkey = _required_external_text(declaration, "pubkey")
                    display_name = _required_external_text(declaration, "display_name")
                except (KeyError, TypeError, ValueError):
                    await self._reject_registration(
                        ctx,
                        request.agent_id,
                        "bad_declaration",
                    )
                    continue
                ctx.emit(
                    NewEvent(
                        EXTERNAL_REGISTRATION_REQUESTED,
                        {key: value for key, value in declaration.items() if key != "challenge"},
                    )
                )
                if self.demography is None:
                    await self._reject_registration(
                        ctx,
                        request.agent_id,
                        "bad_declaration",
                    )
                    continue
                try:
                    agent, _events = self.demography.institution.migration.admit_external(
                        agent_id=request.agent_id,
                        pubkey=pubkey,
                        display_name=display_name,
                        tick=ctx.tick,
                    )
                except (KeyError, TypeError, ValueError):
                    await self._reject_registration(
                        ctx,
                        request.agent_id,
                        "bad_declaration",
                    )
                    continue
                embodiment = self.settings.gateway.registration.embodiment
                ctx.emit(
                    NewEvent(
                        EXTERNAL_AGENT_REGISTERED,
                        {
                            "agent_id": agent.agent_id,
                            "pubkey": agent.pubkey,
                            "operator": declaration.get("operator"),
                            "declared_model": declaration.get("declared_model"),
                            "declared_scaffold": declaration.get("declared_scaffold"),
                            "embodiment": embodiment,
                            "twin_agent_id": None,
                            "conformance_token": declaration.get("conformance_token"),
                            "admitted_tick": ctx.tick,
                            "declaration": {
                                key: value
                                for key, value in declaration.items()
                                if key != "challenge"
                            },
                        },
                        actor_id=agent.agent_id,
                    )
                )
                await self._publish_external_admission(
                    ctx,
                    agent.agent_id,
                    {
                        "status": "admitted",
                        "agent_id": agent.agent_id,
                        "pubkey": agent.pubkey,
                        "operator": declaration.get("operator"),
                        "admitted_tick": ctx.tick,
                        "twin_agent_id": None,
                        "revoked_tick": None,
                        "naturalised_tick": None,
                        "resume_grace_until_tick": None,
                    },
                )
                continue
            if request.request_type == "session_open":
                declaration = request.declaration
                try:
                    session_payload = _external_session_open_payload(declaration)
                except (KeyError, TypeError, ValueError):
                    self._emit_malformed_lifecycle(ctx, request.agent_id, "session_open")
                    continue
                ctx.emit(
                    NewEvent(
                        EXTERNAL_SESSION_OPENED,
                        {
                            "agent_id": request.agent_id,
                            **session_payload,
                        },
                        actor_id=request.agent_id,
                    )
                )
                continue
            if request.request_type == "session_close":
                declaration = request.declaration
                try:
                    session_id = _required_external_text(declaration, "session_id")
                    reason = _required_external_text(declaration, "reason")
                except (KeyError, TypeError, ValueError):
                    self._emit_malformed_lifecycle(ctx, request.agent_id, "session_close")
                    continue
                ctx.emit(
                    NewEvent(
                        EXTERNAL_SESSION_CLOSED,
                        {
                            "agent_id": request.agent_id,
                            "session_id": session_id,
                            "reason": reason,
                        },
                        actor_id=request.agent_id,
                    )
                )
                continue
            if request.request_type == "audit":
                declaration = request.declaration
                injection = declaration.get("injection")
                if isinstance(injection, Mapping):
                    ctx.emit(
                        NewEvent(
                            EXTERNAL_INJECTION_FLAGGED,
                            {
                                "agent_id": request.agent_id,
                                "direction": str(injection.get("direction", "unknown")),
                                "channel": str(injection.get("channel", "unknown")),
                                "source_ref": str(declaration.get("source_ref", "")),
                                "pattern_id": str(injection.get("pattern_id", "unknown")),
                                "sample_hash": str(declaration.get("sample_hash", "")),
                                "action_taken": str(injection.get("action_taken", "none")),
                            },
                            actor_id=request.agent_id,
                        )
                    )
                sim_aware = declaration.get("sim_aware")
                if isinstance(sim_aware, Mapping):
                    ctx.emit(
                        NewEvent(
                            EXTERNAL_SIM_AWARE_FLAGGED,
                            {
                                "agent_id": request.agent_id,
                                "tick": ctx.tick,
                                "surface": str(sim_aware.get("surface", "unknown")),
                                "confidence": _external_float(
                                    sim_aware.get("confidence"),
                                ),
                                "sample_hash": str(declaration.get("sample_hash", "")),
                            },
                            actor_id=request.agent_id,
                        )
                    )
                continue
            if request.agent_id not in self.population.agents:
                continue
            if request.request_type in {"revoke", "depart"}:
                reason = "revoked" if request.request_type == "revoke" else "departed"
                if request.request_type == "revoke":
                    ctx.emit(
                        NewEvent(
                            EXTERNAL_KEY_REVOKED,
                            {
                                "agent_id": request.agent_id,
                                "revoked_by": request.revoked_by or "operator",
                                "reason": request.reason or "operator request",
                                "strikes": 0,
                            },
                            actor_id=request.agent_id,
                        )
                    )
                self._naturalise_external(ctx, request.agent_id, reason)
                await self._publish_external_admission(
                    ctx,
                    request.agent_id,
                    {
                        "status": "revoked" if request.request_type == "revoke" else "naturalised",
                        "agent_id": request.agent_id,
                        "revoked_tick": (ctx.tick if request.request_type == "revoke" else None),
                        "naturalised_tick": ctx.tick,
                        "resume_grace_until_tick": (
                            ctx.tick + self.settings.gateway.lifecycle.resume_grace_ticks
                        ),
                    },
                )
            elif request.request_type == "resume":
                naturalised_tick = self._naturalised_at.get(request.agent_id)
                grace = self.settings.gateway.lifecycle.resume_grace_ticks
                if naturalised_tick is None or ctx.tick - naturalised_tick > grace:
                    continue
                self._naturalised_external.discard(request.agent_id)
                self._naturalised_at.pop(request.agent_id, None)
                self._external_misses[request.agent_id] = 0
                ctx.emit(
                    NewEvent(
                        EXTERNAL_CONTROL_RESUMED,
                        {
                            "agent_id": request.agent_id,
                            "gap_ticks": ctx.tick - naturalised_tick,
                            "session_id": "pending",
                        },
                        actor_id=request.agent_id,
                    )
                )
                await self._publish_external_admission(
                    ctx,
                    request.agent_id,
                    {
                        "status": "admitted",
                        "agent_id": request.agent_id,
                        "admitted_tick": ctx.tick,
                        "naturalised_tick": None,
                        "resume_grace_until_tick": None,
                    },
                )

    async def _reject_registration(
        self,
        ctx: TickContext,
        agent_id: str,
        reason: str,
    ) -> None:
        ctx.emit(
            NewEvent(
                EXTERNAL_REGISTRATION_REJECTED,
                {"pubkey": None, "reason": reason},
            )
        )
        await self._publish_external_admission(
            ctx,
            agent_id,
            {"status": "rejected", "agent_id": agent_id, "reason": reason},
        )

    def _emit_malformed_lifecycle(
        self,
        ctx: TickContext,
        agent_id: str,
        request_type: str,
    ) -> None:
        ctx.emit(
            NewEvent(
                EXTERNAL_GATEWAY_DEGRADED,
                {
                    "reason": f"malformed_lifecycle:{request_type}",
                    "affected_agent_ids": [agent_id],
                    "tick": ctx.tick,
                },
            )
        )

    async def _publish_external_admission(
        self,
        ctx: TickContext,
        agent_id: str,
        status: Mapping[str, Any],
    ) -> None:
        if self.external_decisions is None:
            return
        try:
            await self.external_decisions.publish_admission(agent_id, status)
        except (OSError, TimeoutError):
            ctx.emit(
                NewEvent(
                    EXTERNAL_GATEWAY_DEGRADED,
                    {
                        "reason": "admission_publish_failed",
                        "affected_agent_ids": [agent_id],
                        "tick": ctx.tick,
                    },
                )
            )

    async def demography_vitals(self, ctx: TickContext) -> None:
        if self.demography is not None:
            await self.demography.institution.run(ctx.tick)
        if self.economy is not None:
            self.economy.sync_denormalised(self.population)
            self.economy.ledger.commit_tick(ctx.tick)

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
        if self.external_decisions is not None:
            controlled = self._controlled_external_ids()
            if ctx.tick == 1 and controlled and self.settings.gateway.deadline.pause_for_external:
                ctx.emit(
                    NewEvent(
                        EXTERNAL_ARENA_INVALIDATED,
                        {
                            "reason": "paused_for_external",
                            "offending_agent_ids": sorted(controlled),
                            "threshold": self.settings.gateway.deadline.pause_max_ms,
                            "observed": True,
                        },
                    )
                )
            try:
                await self.external_decisions.open_tick(
                    ctx.tick,
                    sim_time=ctx.sim_time.isoformat(),
                    decision_deadline_ms=(self.settings.gateway.deadline.decision_deadline_ms),
                    seal_margin_ms=self.settings.gateway.deadline.seal_margin_ms,
                )
            except (OSError, TimeoutError):
                ctx.emit(
                    NewEvent(
                        EXTERNAL_GATEWAY_DEGRADED,
                        {
                            "reason": "tick_open_failed",
                            "affected_agent_ids": sorted(controlled),
                            "tick": ctx.tick,
                        },
                    )
                )
            published = await asyncio.gather(
                *(
                    self.external_decisions.publish_observation(
                        ctx.tick,
                        agent_id,
                        canonical_bytes(self.observations[agent_id].as_dict()),
                    )
                    for agent_id in sorted(controlled)
                ),
                return_exceptions=True,
            )
            failed = [
                agent_id
                for agent_id, outcome in zip(sorted(controlled), published, strict=True)
                if isinstance(outcome, BaseException) or not outcome
            ]
            if failed:
                ctx.emit(
                    NewEvent(
                        EXTERNAL_GATEWAY_DEGRADED,
                        {
                            "reason": "obs_write_failed",
                            "affected_agent_ids": failed,
                            "tick": ctx.tick,
                        },
                    )
                )
            sample_rate = self.settings.gateway.security.external_obs_sample_rate
            for agent_id in sorted(controlled - set(failed)):
                sampled = (
                    self.rng.seed_for("gateway.obs.sample", agent_id, ctx.tick) / (2**64 - 1)
                    < sample_rate
                )
                if sampled:
                    blob = canonical_bytes(self.observations[agent_id].as_dict())
                    ctx.emit(
                        NewEvent(
                            EXTERNAL_OBSERVATION_PUSHED,
                            {
                                "agent_id": agent_id,
                                "tick": ctx.tick,
                                "digest_hash": self.observations[agent_id].digest_hash,
                                "bytes": len(blob),
                                "channel": "poll",
                            },
                            actor_id=agent_id,
                        )
                    )

    async def salience(self, ctx: TickContext) -> None:
        self.routing = route_cognition(
            self.population,
            self.observations,
            self.memory,
            settings=self.settings,
            rng=self.rng,
            excluded_agent_ids=frozenset(self._controlled_external_ids()),
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
        external_task = asyncio.create_task(self._guard_external_decide(ctx))
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
        deliberate_results, reflection_results, _ = await asyncio.gather(
            asyncio.gather(*deliberate_tasks),
            asyncio.gather(*reflect_tasks),
            external_task,
        )
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

    def _controlled_external_ids(self) -> set[str]:
        if self.external_decisions is None:
            return set()
        return {
            agent_id
            for agent_id in self.external_decisions.controlled_agent_ids()
            if agent_id in self.population.agents
            and self.population[agent_id].alive
            and agent_id not in self._naturalised_external
        }

    async def _guard_external_decide(self, ctx: TickContext) -> None:
        try:
            await self._decide_external(ctx)
        except Exception:
            affected = sorted(self._controlled_external_ids())
            ctx.emit(
                NewEvent(
                    EXTERNAL_GATEWAY_DEGRADED,
                    {
                        "reason": "external_decision_error",
                        "affected_agent_ids": affected,
                        "tick": ctx.tick,
                    },
                )
            )
            for agent_id in affected:
                if agent_id not in self.decisions:
                    self._fallback_external(ctx, agent_id)

    async def _decide_external(self, ctx: TickContext) -> None:
        if self.external_decisions is None:
            return
        if not self._controlled_external_ids():
            return
        timeout_ms = self.settings.gateway.deadline.drain_timeout_ms
        total_timeout_ms = self.settings.gateway.deadline.decision_deadline_ms + timeout_ms
        if self.settings.gateway.deadline.pause_for_external:
            total_timeout_ms = self.settings.gateway.deadline.pause_max_ms + timeout_ms
        try:
            batch = await asyncio.wait_for(
                self.external_decisions.drain_actions(ctx.tick, timeout_ms=timeout_ms),
                timeout=total_timeout_ms / 1_000 + 0.25,
            )
        except (TimeoutError, OSError):
            batch = None
        if batch is None:
            affected = sorted(self._controlled_external_ids())
            ctx.emit(
                NewEvent(
                    EXTERNAL_GATEWAY_DEGRADED,
                    {
                        "reason": "drain_timeout",
                        "affected_agent_ids": affected,
                        "tick": ctx.tick,
                    },
                )
            )
            records: tuple[ExternalAction, ...] = ()
        else:
            records = batch.actions
            for agent_id in batch.resumed_agent_ids:
                if agent_id in self._naturalised_external:
                    self._naturalised_external.remove(agent_id)
                    self._naturalised_at.pop(agent_id, None)
                    self._external_misses[agent_id] = 0
                    ctx.emit(
                        NewEvent(
                            EXTERNAL_CONTROL_RESUMED,
                            {
                                "agent_id": agent_id,
                                "gap_ticks": 0,
                                "session_id": "gateway",
                            },
                            actor_id=agent_id,
                        )
                    )
            if batch.degraded_reason is not None:
                ctx.emit(
                    NewEvent(
                        EXTERNAL_GATEWAY_DEGRADED,
                        {
                            "reason": batch.degraded_reason,
                            "affected_agent_ids": sorted(self._controlled_external_ids()),
                            "tick": ctx.tick,
                        },
                    )
                )

        by_agent: dict[str, ExternalAction] = {}
        for record in sorted(records, key=lambda row: (row.agent_id, row.action_id, row.nonce)):
            if record.agent_id not in self._controlled_external_ids():
                self._reject_external(ctx, record, "session_invalid")
                continue
            if record.agent_id in by_agent:
                self._reject_external(ctx, record, "no_slots")
                continue
            by_agent[record.agent_id] = record

        for agent_id in sorted(self._controlled_external_ids()):
            submitted = by_agent.get(agent_id)
            if submitted is None:
                self._fallback_external(ctx, agent_id)
                continue
            try:
                action = self._external_action(submitted, ctx.tick)
            except (KeyError, TypeError, ValueError):
                self._reject_external(ctx, submitted, "schema")
                self._fallback_external(ctx, agent_id)
                continue
            self.decisions[agent_id] = action
            self._external_misses[agent_id] = 0
            self._external_ticks_driven[agent_id] = self._external_ticks_driven.get(agent_id, 0) + 1
            injection = submitted.audit.get("injection")
            if isinstance(injection, Mapping):
                ctx.emit(
                    NewEvent(
                        EXTERNAL_INJECTION_FLAGGED,
                        {
                            "agent_id": agent_id,
                            "direction": str(injection.get("direction", "unknown")),
                            "channel": str(injection.get("channel", "unknown")),
                            "source_ref": submitted.action_id,
                            "pattern_id": str(injection.get("pattern_id", "unknown")),
                            "sample_hash": str(injection.get("sample_hash", "")),
                            "action_taken": str(injection.get("action_taken", "none")),
                        },
                        actor_id=agent_id,
                    )
                )
            sim_aware = submitted.audit.get("sim_aware")
            if isinstance(sim_aware, Mapping):
                ctx.emit(
                    NewEvent(
                        EXTERNAL_SIM_AWARE_FLAGGED,
                        {
                            "agent_id": agent_id,
                            "tick": ctx.tick,
                            "surface": str(sim_aware.get("surface", "unknown")),
                            "confidence": _external_float(sim_aware.get("confidence")),
                            "sample_hash": str(sim_aware.get("sample_hash", "")),
                        },
                        actor_id=agent_id,
                    )
                )
            ctx.emit(
                NewEvent(
                    EXTERNAL_ACTION_SUBMITTED,
                    {
                        "agent_id": agent_id,
                        "action_id": submitted.action_id,
                        "tick": ctx.tick,
                        "type": submitted.type,
                        "nonce": submitted.nonce,
                        "params_hash": sha256_hex(canonical_bytes(submitted.params)),
                        "reasoning_hash": (
                            sha256_hex(submitted.reasoning.encode())
                            if submitted.reasoning
                            else None
                        ),
                        "sig": submitted.sig,
                    },
                    actor_id=agent_id,
                    sig=submitted.sig,
                )
            )

    def _external_action(self, record: ExternalAction, tick: int) -> Action:
        if record.tick != tick:
            raise ValueError("external action tick mismatch")
        if record.agent_id not in self.population.agents:
            raise KeyError(record.agent_id)
        return Action(
            UUID(record.action_id),
            record.agent_id,
            tick,
            ActionType(record.type),
            dict(record.params),
            "external",
            0.0,
            record.reasoning,
            record.speech,
            record.sig,
        )

    def _reject_external(
        self,
        ctx: TickContext,
        record: ExternalAction,
        reason: str,
    ) -> None:
        ctx.emit(
            NewEvent(
                EXTERNAL_ACTION_REJECTED,
                {
                    "agent_id": record.agent_id,
                    "action_id": record.action_id,
                    "tick": ctx.tick,
                    "stage": "engine",
                    "reason": reason,
                },
                actor_id=record.agent_id,
            )
        )

    def _fallback_external(self, ctx: TickContext, agent_id: str) -> None:
        misses = self._external_misses.get(agent_id, 0) + 1
        self._external_misses[agent_id] = misses
        self._external_deadlines_missed[agent_id] = (
            self._external_deadlines_missed.get(agent_id, 0) + 1
        )
        self._external_ticks_driven[agent_id] = self._external_ticks_driven.get(agent_id, 0) + 1
        self.decisions[agent_id] = reflex_decide(
            self.population[agent_id],
            self.observations[agent_id],
            self.world,
            rng=self.rng,
            origin="fallback",
        )
        ctx.emit(
            NewEvent(
                EXTERNAL_DEADLINE_MISSED,
                {
                    "agent_id": agent_id,
                    "tick": ctx.tick,
                    "window_ms": self.settings.gateway.deadline.decision_deadline_ms,
                    "consecutive_misses": misses,
                    "fell_back_to": "reflex",
                    "arrived_late_ms": None,
                },
                actor_id=agent_id,
            )
        )
        threshold = self.settings.gateway.lifecycle.naturalise_after_consecutive_misses
        if misses >= threshold:
            self._naturalise_external(ctx, agent_id, "abandoned")

    def _naturalise_external(
        self,
        ctx: TickContext,
        agent_id: str,
        reason: str,
    ) -> None:
        if agent_id in self._naturalised_external:
            return
        self._naturalised_external.add(agent_id)
        self._naturalised_at[agent_id] = ctx.tick
        ctx.emit(
            NewEvent(
                EXTERNAL_AGENT_NATURALISED,
                {
                    "agent_id": agent_id,
                    "reason": reason,
                    "consecutive_misses": self._external_misses.get(agent_id, 0),
                    "ticks_driven": self._external_ticks_driven.get(agent_id, 0),
                    "driver_after": "native",
                    "resume_grace_until_tick": (
                        ctx.tick + self.settings.gateway.lifecycle.resume_grace_ticks
                    ),
                },
                actor_id=agent_id,
            )
        )

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
            if (
                validation.accepted
                and self.demography is not None
                and action.type in self.demography.resolver.handles
            ):
                relational_ctx = ValidationContext(
                    self.observations[agent_id],
                    self.population,
                    ctx.tick,
                    self.runtime,
                )
                for gate_name, gate in (
                    ("capability", self.demography.resolver.check_capability),
                    ("locality", self.demography.resolver.check_locality),
                    ("resources", self.demography.resolver.check_resources),
                ):
                    failure = gate(action, relational_ctx)
                    if failure is None:
                        continue
                    gates = dict(validation.gates)
                    gates[gate_name] = "fail"
                    validation = Validation(
                        False,
                        action,
                        str(failure.reason),
                        gates,
                        {"reason": failure.detail},
                    )
                    break
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
        if self.demography is not None:
            relational = tuple(
                ValidatedAction(
                    validation.action,
                    PARAMS_MODELS[validation.action.type].model_validate(validation.action.params),
                    LegalityVerdict(False),
                    0,
                )
                for _agent_id, validation in sorted(self.validations.items())
                if validation.accepted
                and validation.action.type in self.demography.resolver.handles
            )
            self.demography.resolver.resolve(
                relational,
                ctx.tick,
                ResolutionContext(ctx.emit, self.runtime),
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
            score = self.routing.scores.get(agent_id) if self.routing is not None else None
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
        if ctx.tick >= self._final_tick and not self._external_arena_checked:
            self._external_arena_checked = True
            invalidation = self._external_arena_invalidation()
            if invalidation is not None:
                ctx.emit(
                    NewEvent(
                        EXTERNAL_ARENA_INVALIDATED,
                        invalidation,
                    )
                )
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
            (
                row
                for row in self.routing.scores.values()
                if row.agent_id in self.world.locations
                and row.agent_id in self.population.agents
                and self.population[row.agent_id].alive
            ),
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

    def _external_arena_invalidation(self) -> Mapping[str, Any] | None:
        threshold = self.settings.research.gates.external_miss_rate_max
        miss_rates = {
            agent_id: self._external_deadlines_missed.get(agent_id, 0) / ticks_driven
            for agent_id, ticks_driven in self._external_ticks_driven.items()
            if ticks_driven > 0
        }
        offending = sorted(
            agent_id for agent_id, miss_rate in miss_rates.items() if miss_rate > threshold
        )
        if not offending:
            return None
        return {
            "reason": "miss_rate",
            "offending_agent_ids": offending,
            "threshold": threshold,
            "observed": max(miss_rates[agent_id] for agent_id in offending),
        }


def _required_external_text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item


def _required_external_int(value: Mapping[str, Any], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return item


def _external_session_open_payload(declaration: Mapping[str, Any]) -> dict[str, Any]:
    custody = _required_external_text(declaration, "custody")
    transport = _required_external_text(declaration, "transport")
    if custody not in {"operator", "delegated"}:
        raise ValueError("invalid custody")
    if transport not in {"mcp_stdio", "mcp_http", "rest", "ws"}:
        raise ValueError("invalid transport")
    client = declaration.get("client")
    if not isinstance(client, Mapping):
        raise ValueError("client must be an object")
    delegate = declaration.get("delegate_pubkey")
    if delegate is not None and not isinstance(delegate, str):
        raise ValueError("delegate_pubkey must be a string or null")
    protocol_version = _required_external_int(declaration, "protocol_version")
    if protocol_version != 1:
        raise ValueError("unsupported protocol version")
    return {
        "session_id": _required_external_text(declaration, "session_id"),
        "custody": custody,
        "delegate_pubkey": delegate,
        "ttl_s": _required_external_int(declaration, "ttl_s"),
        "transport": transport,
        "sdk_version": _required_external_text(declaration, "sdk_version"),
        "protocol_version": protocol_version,
        "expires_unix_ms": _required_external_int(declaration, "expires_unix_ms"),
        "client": dict(client),
    }


def _external_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


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
    completion_cache: CompletionCache | None = None,
    lane_concurrency_overrides: Mapping[str, int] | None = None,
    external_decisions: ExternalDecisionPort | None = None,
    run_identity: RunIdentity | None = None,
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
    runtime_cache = completion_cache or (
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
    total_ticks = ticks if ticks is not None else settings.run.ticks
    initial_cache_manifest_hash = (
        runtime_cache.manifest_hash()
        if runtime_cache is not None
        else EMPTY_COMPLETION_CACHE_MANIFEST_HASH
    )
    if run_identity is None:
        identity = build_run_identity(
            settings,
            completion_cache_manifest_hash=initial_cache_manifest_hash,
        )
    else:
        validate_run_identity(
            settings,
            run_identity,
            completion_cache_manifest_hash=(
                run_identity.completion_cache_manifest_hash
                if runtime_cache is not None and runtime_cache.mode == "replay"
                else initial_cache_manifest_hash
            ),
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
                    "born_tick": agent.born_tick,
                    "home_place_id": agent.home_place_id,
                    "household_id": agent.household_id,
                    "mother_id": agent.mother_id,
                    "father_id": agent.father_id,
                    "generation": agent.generation,
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
    runtime = RuntimeConfig(settings)
    engine = LivingCityEngine(
        settings=settings,
        world=world,
        population=population,
        memory=memory,
        metrics=metrics,
        router=router,
        rng=rng,
        economy=economy,
        log=log,
        clock=clock,
        runtime=runtime,
        external_decisions=external_decisions,
        final_tick=total_ticks,
    )
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
    try:
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
        else:
            await router.start()
            report = await loop.run(total_ticks)
    finally:
        await router.close()
    if not engine._external_arena_checked:
        engine._external_arena_checked = True
        invalidation = engine._external_arena_invalidation()
        if invalidation is not None:
            log.stage(
                NewEvent(EXTERNAL_ARENA_INVALIDATED, invalidation),
                tick=report.last_tick,
                sim_time=clock.sim_time,
            )
            committed = await log.commit(report.last_tick)
            report = replace(
                report,
                events=report.events + committed.persisted,
                chain_hash=log.chain_hash,
            )
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
        engine.demography,
        router.cache.manifest(),
        router.cache.manifest_hash(),
    )
