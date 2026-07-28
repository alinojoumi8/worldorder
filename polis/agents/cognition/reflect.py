from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polis.agents.actions.types import Action, make_legacy_action
from polis.agents.cognition.observation import Observation
from polis.agents.cognition.reflex import reflex_decide
from polis.agents.memory import MemoryRecord, MemoryStore, ReflectionInsight, Retrieval
from polis.agents.types import AgentState
from polis.kernel.rng import RngRegistry
from polis.llm.purposes import Purpose
from polis.llm.router import CallResult, LLMRouter
from polis.world.api import World


@dataclass(frozen=True, slots=True)
class Reflection:
    action: Action
    memories: tuple[MemoryRecord, ...]
    retrieval: tuple[Retrieval, ...]
    call: CallResult | None


async def reflect_decide(
    agent: AgentState,
    observation: Observation,
    *,
    memory: MemoryStore,
    router: LLMRouter,
    world: World,
    rng: RngRegistry,
    salience: float,
) -> Reflection:
    retrieval = memory.retrieve(
        agent.agent_id, "my situation and priorities", tick=observation.tick
    )
    if not retrieval:
        fallback = reflex_decide(agent, observation, world, rng=rng, origin="fallback")
        return Reflection(fallback, (), (), None)
    allowed_ids = [row.memory_id for row in retrieval]
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "insights": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "statement": {"type": "string", "maxLength": 300},
                        "supported_by": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {"enum": allowed_ids},
                        },
                        "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["statement", "supported_by", "importance"],
                },
            },
            "identity_summary": {"type": "string", "maxLength": 500},
        },
        "required": ["insights", "identity_summary"],
    }
    prompt = "\n".join(
        [
            "Review these recollections and state a supported insight.",
            *(f"[{row.memory_id}] {row.text}" for row in retrieval),
        ]
    )
    call = await router.call(
        Purpose.REFLECT,
        agent.agent_id,
        observation.tick,
        {
            "system": "Reflect on this person's recent experience using only cited recollections.",
            "prompt": prompt,
        },
        schema,
    )
    parsed = call.parsed or {}
    insights = tuple(
        ReflectionInsight(
            str(item["statement"]),
            tuple(str(value) for value in item["supported_by"]),
            float(item["importance"]),
        )
        for item in parsed.get("insights", ())
    )
    rows = memory.apply_reflection(
        agent,
        tick=observation.tick,
        insights=insights,
        identity_summary=str(parsed.get("identity_summary", "")),
    )
    reflex = reflex_decide(agent, observation, world, rng=rng)
    action = make_legacy_action(
        actor_id=reflex.actor_id,
        tick=reflex.tick,
        action_type=reflex.type,
        params=reflex.params,
        origin="reflect",
        salience=salience,
        reasoning="Acting after reflection.",
    )
    return Reflection(action, rows, retrieval, call)
