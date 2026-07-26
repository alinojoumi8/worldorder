from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from polis.agents.cognition.deliberate import deliberate_decide
from polis.agents.cognition.observation import build_observations
from polis.agents.cognition.reflex import reflex_decide
from polis.agents.cognition.salience import route_cognition
from polis.agents.genesis import generate_agents
from polis.agents.memory import MemoryStore
from polis.config.settings import (
    MemorySettings,
    PopulationSettings,
    WorldSettings,
    load_settings,
)
from polis.kernel.rng import RngRegistry
from polis.llm.router import LLMRouter
from polis.world.generator import generate_world


def _population(count: int = 100):
    rng = RngRegistry(44)
    world = generate_world(
        WorldSettings(width=60, height=60, districts=4, places_per_district=12),
        rng,
    )
    population = generate_agents(
        PopulationSettings(initial_agents=count),
        world,
        rng,
    )
    return rng, world, population


def test_weighted_routing_hits_the_intended_deliberate_share() -> None:
    rng, world, population = _population()
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "population": {"initial_agents": 100},
            "run": {"scale": 100},
            "llm": {
                "budget": {
                    "lines": {
                        "cognition": {
                            "calls_per_tick": 10,
                            "tokens_per_tick": 33000,
                        }
                    }
                }
            },
        },
    )
    observations = build_observations(
        population,
        world,
        tick=1,
        sim_time=datetime(2100, 1, 2),
    )

    result = route_cognition(
        population,
        observations,
        MemoryStore(MemorySettings()),
        settings=settings,
        rng=rng,
    )

    assert result.n_deliberate == 7
    assert result.n_reflex == 93
    assert result.n_reflect == 0
    assert result.cutoff < 1
    assert sorted(score.rank for score in result.scores.values()) == list(range(1, 101))


def test_chronic_unmet_need_is_not_misclassified_as_event_stakes() -> None:
    rng, world, population = _population()
    agent = population.alive()[0]
    agent.needs.social = 0
    settings = load_settings(Path("configs/smoke.yaml"))
    observations = build_observations(
        population,
        world,
        tick=1,
        sim_time=datetime(2100, 1, 2),
    )

    result = route_cognition(
        population,
        observations,
        MemoryStore(MemorySettings()),
        settings=settings,
        rng=rng,
    )

    assert observations[agent.agent_id].stakes == 0
    assert result.scores[agent.agent_id].components["stakes"] == 0


def test_reflection_backlog_preserves_deliberate_reserve() -> None:
    rng, world, population = _population(50)
    settings = load_settings(Path("configs/smoke.yaml"))
    for agent in population.alive():
        agent.importance_since_reflection = 5.0
    observations = build_observations(
        population,
        world,
        tick=25,
        sim_time=datetime(2100, 1, 26),
    )

    result = route_cognition(
        population,
        observations,
        MemoryStore(MemorySettings()),
        settings=settings,
        rng=rng,
    )

    assert result.n_deliberate == 4
    assert result.n_reflect == 6
    assert result.n_reflex == 40


def test_reflex_decision_is_repeatable_for_agent_tick() -> None:
    _rng, world, population = _population(2)
    observations = build_observations(
        population,
        world,
        tick=4,
        sim_time=datetime(2100, 1, 5),
    )

    first = reflex_decide(
        population["ag_0000"],
        observations["ag_0000"],
        world,
        rng=RngRegistry(44),
    )
    second = reflex_decide(
        population["ag_0000"],
        observations["ag_0000"],
        world,
        rng=RngRegistry(44),
    )

    assert first == second


@pytest.mark.asyncio
async def test_deliberate_stub_call_is_structured_and_prompt_disciplined() -> None:
    _rng, world, population = _population(2)
    observation = build_observations(
        population,
        world,
        tick=4,
        sim_time=datetime(2100, 1, 5),
    )["ag_0000"]
    router = LLMRouter(
        settings=load_settings(Path("configs/smoke.yaml")),
        run_id=UUID("20000000-0000-0000-0000-000000000009"),
    )

    result = await deliberate_decide(
        population["ag_0000"],
        observation,
        (),
        router=router,
        salience=0.7,
    )

    assert result.call.parsed_ok
    assert result.action.actor_id == "ag_0000"
    assert all(word not in result.prompt.lower() for word in ("simulation", " ai ", "model"))
