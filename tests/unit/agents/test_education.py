from __future__ import annotations

from polis.agents.actions.types import ActionType, make_action
from polis.agents.education import apply_education
from polis.agents.genesis import generate_agents
from polis.config.settings import PopulationSettings, WorldSettings
from polis.kernel.rng import RngRegistry
from polis.world.api import Location
from polis.world.generator import generate_world


def test_study_accrues_bounded_skills_at_an_afforded_place() -> None:
    world = generate_world(
        WorldSettings(width=60, height=60, districts=4, places_per_district=15),
        RngRegistry(55),
    )
    population = generate_agents(
        PopulationSettings(initial_agents=2),
        world,
        RngRegistry(55),
    )
    agent = population["ag_0000"]
    school = world.places_of_type("school")[0]
    world.locations[agent.agent_id] = Location(
        school.place_id,
        school.district_id,
        school.x,
        school.y,
    )
    before = dict(agent.skills)

    deltas = apply_education(
        (
            make_action(
                actor_id=agent.agent_id,
                tick=1,
                action_type=ActionType.STUDY,
            ),
        ),
        population=population,
        world=world,
        ticks_per_day=24,
    )

    assert deltas
    assert all(delta.after > delta.before for delta in deltas)
    assert agent.skills["writing"] > before["writing"]
    assert agent.skills["engineering"] == before["engineering"]
