from __future__ import annotations

from dataclasses import dataclass

from polis.agents.cognition.observation import Observation
from polis.agents.memory import MemoryStore
from polis.agents.state import AgentPopulation
from polis.agents.types import CognitionMode
from polis.config.settings import Settings
from polis.kernel.rng import RngRegistry


@dataclass(frozen=True, slots=True)
class SalienceScore:
    agent_id: str
    score: float
    components: dict[str, float]
    rank: int = 0
    routed_mode: CognitionMode = "reflex"


@dataclass(frozen=True, slots=True)
class RoutingResult:
    scores: dict[str, SalienceScore]
    modes: dict[str, CognitionMode]
    cutoff: float
    n_reflex: int
    n_deliberate: int
    n_reflect: int


def _jaccard_distance(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 0
    union = left | right
    return 1 - len(left & right) / len(union)


def _score(
    population: AgentPopulation,
    observation: Observation,
    *,
    settings: Settings,
    rng: RngRegistry,
) -> SalienceScore:
    agent = population[observation.agent_id]
    surprise = _jaccard_distance(
        observation.digest_features,
        agent.expectation_features or observation.digest_features,
    )
    # Stakes describe a change in the current event, not chronic unmet needs.
    # M1 observations emit zero until owning milestones provide wealth, health,
    # employment, relationship, or legal-jeopardy deltas.
    stakes = min(1.0, observation.stakes * (1 + agent.traits.neuroticism))
    situation = f"{observation.place.type}|{agent.employment_status}"
    novelty = (1.0 if situation not in agent.seen_situations else 0.0) * agent.traits.openness
    social = 0.4 if observation.co_located else 0.0
    scheduled = 1.0 if observation.obligations else 0.0
    components = {
        "surprise": surprise,
        "stakes": stakes,
        "novelty": novelty,
        "social": social,
        "scheduled": scheduled,
    }
    weighted = sum(settings.salience.weights[name] * value for name, value in components.items())
    epsilon = (
        rng.get("salience.exploration", agent.agent_id, observation.tick).random()
        * settings.salience.exploration_epsilon
    )
    return SalienceScore(
        agent.agent_id,
        round(min(1.0, weighted + epsilon), 8),
        {key: round(value, 8) for key, value in components.items()},
    )


def route_cognition(
    population: AgentPopulation,
    observations: dict[str, Observation],
    memory: MemoryStore,
    *,
    settings: Settings,
    rng: RngRegistry,
) -> RoutingResult:
    raw = {
        agent_id: _score(population, observation, settings=settings, rng=rng)
        for agent_id, observation in sorted(observations.items())
    }
    ranked = sorted(raw.values(), key=lambda item: (-item.score, item.agent_id))
    cognition_line = settings.llm.budget.lines["cognition"]
    intended = round(len(ranked) * settings.salience.deliberate_share)
    reflection_candidates = sorted(
        (
            agent
            for agent in population.alive()
            if memory.reflection_due(agent, tick=observations[agent.agent_id].tick)
        ),
        key=lambda agent: (-agent.importance_since_reflection, agent.agent_id),
    )
    # M1 reflection consumes one call. Keep synchronized trigger bursts inside
    # the reserve so they cannot erase the calibrated deliberate lane.
    reflection_capacity = max(0, cognition_line.calls_per_tick - intended)
    reflect_ids = {agent.agent_id for agent in reflection_candidates[:reflection_capacity]}
    available = max(0, cognition_line.calls_per_tick - len(reflect_ids))
    target = min(intended, available)
    candidates = [score for score in ranked if score.agent_id not in reflect_ids]
    if settings.ablations.reflex_only:
        selected: list[SalienceScore] = []
        reflect_ids.clear()
    elif settings.salience.policy == "always":
        selected = candidates[:available]
    elif settings.salience.policy == "random":
        selected = sorted(
            candidates,
            key=lambda item: (
                rng.seed_for("salience.random", item.agent_id, observations[item.agent_id].tick),
                item.agent_id,
            ),
        )[:target]
    else:
        selected = candidates[:target]
    deliberate_ids = {item.agent_id for item in selected}
    modes: dict[str, CognitionMode] = {}
    scored: dict[str, SalienceScore] = {}
    rank_by_id = {item.agent_id: rank for rank, item in enumerate(ranked, 1)}
    for agent_id, item in raw.items():
        mode: CognitionMode = (
            "reflect"
            if agent_id in reflect_ids
            else "deliberate"
            if agent_id in deliberate_ids
            else "reflex"
        )
        population[agent_id].cognition_mode = mode
        population[agent_id].expectation_features = observations[agent_id].digest_features
        population[agent_id].seen_situations.add(
            f"{observations[agent_id].place.type}|{population[agent_id].employment_status}"
        )
        modes[agent_id] = mode
        scored[agent_id] = SalienceScore(
            item.agent_id,
            item.score,
            item.components,
            rank_by_id[agent_id],
            mode,
        )
    cutoff = min((raw[agent_id].score for agent_id in deliberate_ids), default=1.0)
    return RoutingResult(
        scored,
        modes,
        cutoff,
        sum(mode == "reflex" for mode in modes.values()),
        sum(mode == "deliberate" for mode in modes.values()),
        sum(mode == "reflect" for mode in modes.values()),
    )
