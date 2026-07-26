from __future__ import annotations

from polis.agents.genesis import generate_agents
from polis.config.settings import PopulationSettings, WorldSettings
from polis.kernel.rng import RngRegistry
from polis.world.generator import generate_world


def test_agent_genesis_is_deterministic_bounded_and_located() -> None:
    world_settings = WorldSettings(width=40, height=40, districts=4, places_per_district=8)
    first_world = generate_world(world_settings, RngRegistry(5))
    second_world = generate_world(world_settings, RngRegistry(5))

    first = generate_agents(PopulationSettings(initial_agents=50), first_world, RngRegistry(5))
    second = generate_agents(
        PopulationSettings(initial_agents=50),
        second_world,
        RngRegistry(5),
    )

    assert first.dump() == second.dump()
    assert len(first) == 50
    assert tuple(first.agents) == tuple(f"ag_{index:04d}" for index in range(50))
    assert set(first.agents) == set(first_world.locations)
    for agent in first:
        assert all(0 <= value <= 1 for value in agent.traits.as_dict().values())
        assert all(value == round(value, 12) for value in agent.traits.as_dict().values())
        assert all(0 <= value <= 1 for value in agent.skills.values())
        assert agent.home_place_id == first_world.locations[agent.agent_id].place_id


def test_population_checkpoint_roundtrip() -> None:
    world = generate_world(
        WorldSettings(width=30, height=30, districts=3, places_per_district=6),
        RngRegistry(9),
    )
    population = generate_agents(
        PopulationSettings(initial_agents=12),
        world,
        RngRegistry(9),
    )
    snapshot = population.dump()
    population["ag_0000"].needs.energy = 0

    population.load(snapshot)

    assert population["ag_0000"].needs.energy == 0.8
    assert population.population() == 12
