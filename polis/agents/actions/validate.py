from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from polis.agents.actions.types import Action, ActionType, null_action
from polis.agents.types import AgentState
from polis.kernel.clock import ClockProfile
from polis.world.api import World

GateResult = Literal["pass", "fail", "clean", "flagged", "not_checked"]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoveParams(_Params):
    place_id: str


class EmptyParams(_Params):
    pass


_PARAM_MODELS: dict[ActionType, type[_Params]] = {
    ActionType.MOVE_TO: MoveParams,
    ActionType.IDLE: EmptyParams,
    ActionType.SLEEP: EmptyParams,
    ActionType.EAT: EmptyParams,
    ActionType.STUDY: EmptyParams,
    ActionType.NULL_ACTION: EmptyParams,
}


@dataclass(slots=True)
class ActionBudget:
    slots_per_agent: int
    used: dict[str, int] = field(default_factory=dict)

    @classmethod
    def for_profile(cls, profile: ClockProfile) -> ActionBudget:
        return cls(4 if profile.ticks_per_sim_day == 1 else 1)

    def available(self, agent_id: str) -> bool:
        return self.used.get(agent_id, 0) < self.slots_per_agent

    def consume(self, agent_id: str) -> bool:
        if not self.available(agent_id):
            return False
        self.used[agent_id] = self.used.get(agent_id, 0) + 1
        return True


@dataclass(frozen=True, slots=True)
class Validation:
    accepted: bool
    action: Action
    reason: str | None
    gates: dict[str, GateResult]
    detail: dict[str, Any]


def _reject(
    action: Action,
    reason: str,
    gates: dict[str, GateResult],
    detail: dict[str, Any] | None = None,
) -> Validation:
    return Validation(
        False,
        null_action(action, reasoning=f"rejected: {reason}"),
        reason,
        gates,
        detail or {},
    )


def validate_action(
    action: Action,
    *,
    agent: AgentState,
    world: World,
    profile: ClockProfile,
    budget: ActionBudget,
) -> Validation:
    gates: dict[str, GateResult] = {
        "schema": "not_checked",
        "capability": "not_checked",
        "locality": "not_checked",
        "resources": "not_checked",
        "legality": "not_checked",
    }
    try:
        params = _PARAM_MODELS[action.type].model_validate(action.params)
    except (KeyError, ValidationError) as exc:
        gates["schema"] = "fail"
        return _reject(action, "schema", gates, {"error": str(exc)})
    gates["schema"] = "pass"

    if agent.employment_status == "child" and action.type not in {
        ActionType.IDLE,
        ActionType.SLEEP,
        ActionType.EAT,
        ActionType.STUDY,
        ActionType.NULL_ACTION,
    }:
        gates["capability"] = "fail"
        return _reject(action, "capability", gates)
    gates["capability"] = "pass"

    location = world.locations[action.actor_id]
    if action.type == ActionType.MOVE_TO:
        target_id = str(params.model_dump()["place_id"])
        if not world.has_place(target_id):
            gates["locality"] = "fail"
            return _reject(action, "locality", gates, {"reason": "unknown_place"})
        if target_id != agent.home_place_id and not world.is_open(target_id, action.tick, profile):
            gates["locality"] = "fail"
            return _reject(action, "locality", gates, {"reason": "closed"})
    elif not world.affords(location.place_id, action.type.value):
        gates["locality"] = "fail"
        return _reject(action, "locality", gates)
    gates["locality"] = "pass"

    if not budget.consume(action.actor_id):
        gates["resources"] = "fail"
        return _reject(action, "resources", gates, {"reason": "action_slots"})
    gates["resources"] = "pass"
    gates["legality"] = "clean"
    return Validation(True, action, None, gates, {})
