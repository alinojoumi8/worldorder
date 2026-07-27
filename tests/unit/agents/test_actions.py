from __future__ import annotations

from polis.agents.actions.resolve import resolve_actions
from polis.agents.actions.types import ActionType, make_action
from polis.agents.actions.validate import (
    ActionBudget,
    action_response_schema,
    validate_action,
)
from polis.agents.genesis import generate_agents
from polis.config.settings import PopulationSettings, WorldSettings
from polis.kernel.clock import PROFILES
from polis.kernel.rng import RngRegistry
from polis.world.generator import generate_world


def _city():
    world = generate_world(
        WorldSettings(width=40, height=40, districts=4, places_per_district=8),
        RngRegistry(21),
    )
    population = generate_agents(
        PopulationSettings(initial_agents=5),
        world,
        RngRegistry(21),
    )
    return world, population


def test_action_validation_rejects_unknown_move_and_consumes_one_slot() -> None:
    world, population = _city()
    profile = PROFILES["microscope"]
    budget = ActionBudget.for_profile(profile)
    invalid = make_action(
        actor_id="ag_0001",
        tick=1,
        action_type=ActionType.MOVE_TO,
        params={"place_id": "pl_missing"},
    )
    valid = make_action(
        actor_id="ag_0001",
        tick=1,
        action_type=ActionType.IDLE,
    )

    rejected = validate_action(
        invalid,
        agent=population["ag_0001"],
        world=world,
        profile=profile,
        budget=budget,
    )
    accepted = validate_action(
        valid,
        agent=population["ag_0001"],
        world=world,
        profile=profile,
        budget=budget,
    )

    assert rejected.reason == "locality"
    assert rejected.action.type == ActionType.NULL_ACTION
    assert accepted.accepted


def test_action_response_schema_is_scoped_and_typed() -> None:
    schema = action_response_schema(("IDLE", "SUBMIT_ORDER", "IDLE"))
    branches = schema["properties"]["action"]["oneOf"]

    assert [branch["properties"]["type"]["const"] for branch in branches] == [
        "IDLE",
        "SUBMIT_ORDER",
    ]
    order_params = branches[1]["properties"]["params"]
    assert {"symbol", "side", "qty"} <= set(order_params["required"])
    assert order_params["properties"]["order_type"]["default"] == "limit"


def test_resolver_restores_needs_without_money() -> None:
    world, population = _city()
    agent = population["ag_0001"]
    agent.needs.energy = 0.2
    action = make_action(
        actor_id=agent.agent_id,
        tick=1,
        action_type=ActionType.SLEEP,
    )

    resolution = resolve_actions(
        (action,),
        population=population,
        world=world,
        tick=1,
        profile=PROFILES["microscope"],
        rng=RngRegistry(21),
    )

    assert agent.needs.energy == 0.34
    assert resolution.restored == (("ag_0001", "energy", 0.14),)
    assert population.money_supply_cents() == 0
