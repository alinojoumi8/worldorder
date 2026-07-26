from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polis.agents.actions.types import Action, ActionType, make_action
from polis.agents.cognition.observation import Observation
from polis.agents.memory import Retrieval
from polis.agents.types import AgentState
from polis.config.canon import sha256_hex
from polis.llm.purposes import Purpose
from polis.llm.router import CallResult, LLMRouter

ACTION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reasoning": {"type": "string", "maxLength": 300},
        "action": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {
                    "enum": [
                        ActionType.MOVE_TO.value,
                        ActionType.IDLE.value,
                        ActionType.SLEEP.value,
                        ActionType.EAT.value,
                        ActionType.STUDY.value,
                        ActionType.NULL_ACTION.value,
                    ]
                },
                "params": {"type": "object"},
            },
            "required": ["type", "params"],
        },
    },
    "required": ["reasoning", "action"],
}


@dataclass(frozen=True, slots=True)
class Deliberation:
    action: Action
    prompt: str
    prompt_hash: str
    call: CallResult
    retrieval: tuple[Retrieval, ...]


def render_prompt(
    agent: AgentState,
    observation: Observation,
    memories: tuple[Retrieval, ...],
) -> str:
    traits = []
    if agent.traits.conscientiousness > 0.65:
        traits.append("You prefer dependable routines.")
    if agent.traits.openness > 0.65:
        traits.append("You are curious about unfamiliar places.")
    if agent.traits.risk_tolerance < 0.35:
        traits.append("You are cautious about uncertain choices.")
    recollections = (
        "\n".join(f"- [{row.memory_id}] {row.text}" for row in memories)
        or "- Nothing especially relevant comes to mind."
    )
    legal = "\n".join(f"- {name}" for name in observation.place.legal_actions)
    return (
        f"You are {agent.display_name}, age {agent.age_years:.0f}, living in the city.\n"
        f"{' '.join(traits)}\n"
        f"{agent.identity_summary}\n"
        "## Right now\n"
        f"{observation.sim_time.isoformat()} at {observation.place.name}. "
        f"Energy {agent.needs.energy:.2f}; hunger {agent.needs.hunger:.2f}.\n"
        "## What comes to mind\n"
        f"{recollections}\n"
        "## What you can do\n"
        f"{legal}\n"
        "## Decision\n"
        "Choose one action and return the required structured response."
    )


async def deliberate_decide(
    agent: AgentState,
    observation: Observation,
    retrieval: tuple[Retrieval, ...],
    *,
    router: LLMRouter,
    salience: float,
) -> Deliberation:
    prompt = render_prompt(agent, observation, retrieval)
    result = await router.call(
        Purpose.DELIBERATE,
        agent.agent_id,
        observation.tick,
        {
            "system": "Act as this person. Choose only from the available actions.",
            "prompt": prompt,
        },
        ACTION_SCHEMA,
    )
    parsed = result.parsed or {}
    action_row = parsed.get("action", {})
    try:
        action_type = ActionType(str(action_row.get("type", "NULL_ACTION")))
    except ValueError:
        action_type = ActionType.NULL_ACTION
    params = dict(action_row.get("params", {}))
    if action_type == ActionType.MOVE_TO and "place_id" not in params:
        params["place_id"] = agent.home_place_id
    action = make_action(
        actor_id=agent.agent_id,
        tick=observation.tick,
        action_type=action_type,
        params=params,
        origin="deliberate",
        salience=salience,
        reasoning=str(parsed.get("reasoning", "")),
    )
    return Deliberation(action, prompt, sha256_hex(prompt.encode()), result, retrieval)
