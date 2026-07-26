from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from polis.agents.actions.types import Action, ActionType
from polis.agents.state import AgentPopulation
from polis.agents.types import SKILLS, Skill
from polis.world.api import World

_CURRICULA: Final[dict[str, frozenset[Skill]]] = {
    "school": frozenset({"writing", "manual", "operations", "teaching", "persuasion"}),
    "university": frozenset(
        {
            "engineering",
            "research",
            "law",
            "medicine",
            "finance",
            "management",
            "design",
        }
    ),
    "home": frozenset(SKILLS),
}


@dataclass(frozen=True, slots=True)
class SkillDelta:
    agent_id: str
    skill: Skill
    before: float
    after: float
    delta: float
    place_id: str


def _age_factor(age: float) -> float:
    if age <= 18:
        return 0.55 + 0.45 * age / 18
    if age <= 35:
        return 1.0 - 0.2 * (age - 18) / 17
    return max(0.25, 0.8 - 0.012 * (age - 35))


def apply_education(
    actions: Iterable[Action],
    *,
    population: AgentPopulation,
    world: World,
    ticks_per_day: int,
) -> tuple[SkillDelta, ...]:
    deltas: list[SkillDelta] = []
    for action in sorted(actions, key=lambda item: (item.actor_id, item.action_id.hex)):
        if action.type != ActionType.STUDY:
            continue
        agent = population[action.actor_id]
        location = world.locations[action.actor_id]
        if location.place_id is None:
            continue
        place = world.place(location.place_id)
        curriculum = _CURRICULA.get(place.type)
        if curriculum is None:
            continue
        quality = (
            world.district(place.district_id).school_quality
            if place.type in {"school", "university"}
            else 0.45
        )
        learning_rate = (
            (0.5 + 0.5 * agent.traits.conscientiousness)
            * _age_factor(agent.age_years)
            / ticks_per_day
        )
        for skill in sorted(curriculum):
            before = agent.skills[skill]
            delta = quality * 0.035 * learning_rate * (1 - before)
            after = min(1.0, before + delta)
            agent.skills[skill] = after
            deltas.append(
                SkillDelta(
                    agent.agent_id,
                    skill,
                    round(before, 8),
                    round(after, 8),
                    round(after - before, 8),
                    place.place_id,
                )
            )
    return tuple(deltas)
