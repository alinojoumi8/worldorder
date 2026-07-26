from __future__ import annotations

import math

from polis.agents.actions.types import Action, ActionType, make_action
from polis.agents.cognition.observation import Observation
from polis.agents.types import AgentState
from polis.kernel.rng import RngRegistry
from polis.world.api import World


def _candidates(
    agent: AgentState,
    observation: Observation,
    world: World,
) -> list[tuple[ActionType, dict[str, object], float]]:
    rows: list[tuple[ActionType, dict[str, object], float]] = [
        (ActionType.IDLE, {}, 0.15),
    ]
    legal = set(observation.place.legal_actions)
    if "SLEEP" in legal:
        rows.append(
            (
                ActionType.SLEEP,
                {},
                (1 - agent.needs.energy) * agent.reflex_profile.sleep_weight,
            )
        )
    if "EAT" in legal:
        rows.append(
            (
                ActionType.EAT,
                {},
                (1 - agent.needs.hunger) * agent.reflex_profile.eat_weight,
            )
        )
    if "STUDY" in legal and agent.employment_status in {"child", "student"}:
        rows.append(
            (
                ActionType.STUDY,
                {},
                (1 - max(agent.skills.values())) * agent.reflex_profile.study_weight,
            )
        )
    if observation.place.place_id != agent.home_place_id and agent.needs.energy < 0.45:
        rows.append(
            (
                ActionType.MOVE_TO,
                {"place_id": agent.home_place_id},
                1.2 * (1 - agent.needs.energy),
            )
        )
    elif agent.needs.energy > 0.6:
        places = world.places_of_type("park", "school", "university", "shop")
        if places:
            index = int(agent.agent_id.removeprefix("ag_")) % len(places)
            rows.append(
                (
                    ActionType.MOVE_TO,
                    {"place_id": places[index].place_id},
                    0.12 * agent.reflex_profile.explore_weight,
                )
            )
    return rows


def reflex_decide(
    agent: AgentState,
    observation: Observation,
    world: World,
    *,
    rng: RngRegistry,
    origin: str = "reflex",
) -> Action:
    candidates = _candidates(agent, observation, world)
    temperature = max(0.05, agent.reflex_profile.temperature)
    weights = [math.exp(min(20, utility / temperature)) for _, _, utility in candidates]
    threshold = rng.get("agent.reflex", agent.agent_id, observation.tick).random() * sum(weights)
    cumulative = 0.0
    selected = candidates[-1]
    for candidate, weight in zip(candidates, weights, strict=True):
        cumulative += weight
        if threshold <= cumulative:
            selected = candidate
            break
    action_type, params, _utility = selected
    return make_action(
        actor_id=agent.agent_id,
        tick=observation.tick,
        action_type=action_type,
        params=dict(params),
        origin="fallback" if origin == "fallback" else "reflex",
    )
