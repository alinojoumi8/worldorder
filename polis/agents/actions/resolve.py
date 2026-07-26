from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from polis.agents.actions.types import Action, ActionType
from polis.agents.state import AgentPopulation
from polis.kernel.clock import ClockProfile
from polis.kernel.rng import RngRegistry
from polis.world.api import World
from polis.world.movement import MovementResult, MoveRequest, resolve_movement


@dataclass(frozen=True, slots=True)
class Resolution:
    movement: MovementResult
    restored: tuple[tuple[str, str, float], ...]


def resolve_actions(
    actions: Iterable[Action],
    *,
    population: AgentPopulation,
    world: World,
    tick: int,
    profile: ClockProfile,
    rng: RngRegistry,
) -> Resolution:
    ordered = tuple(sorted(actions, key=lambda action: (action.actor_id, action.action_id.hex)))
    movement = resolve_movement(
        world,
        (
            MoveRequest(action.actor_id, str(action.params["place_id"]))
            for action in ordered
            if action.type == ActionType.MOVE_TO
        ),
        tick=tick,
        profile=profile,
        rng=rng,
    )
    restored: list[tuple[str, str, float]] = []
    for action in ordered:
        agent = population[action.actor_id]
        location = world.locations[action.actor_id]
        place_type = world.place(location.place_id).type if location.place_id is not None else None
        if action.type == ActionType.SLEEP:
            amount = {"home": 0.14, "hospital": 0.12, "shelter": 0.07}.get(place_type or "", 0)
            agent.restore("energy", amount)
            restored.append((agent.agent_id, "energy", amount))
        elif action.type == ActionType.EAT:
            amount = (
                0.45 if place_type == "home" else 0.55 if place_type in {"shop", "bar"} else 0.30
            )
            agent.restore("hunger", amount)
            restored.append((agent.agent_id, "hunger", amount))
        elif action.type == ActionType.IDLE and place_type == "park":
            agent.restore("energy", 0.03)
            agent.restore("social", 0.02)
            restored.extend(
                (
                    (agent.agent_id, "energy", 0.03),
                    (agent.agent_id, "social", 0.02),
                )
            )
        population.record_action(action.type.value)
    return Resolution(movement, tuple(restored))
