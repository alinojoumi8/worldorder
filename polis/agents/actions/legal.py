from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Final, cast

from polis.agents.actions.params import PARAMS_MODELS
from polis.agents.actions.protocol import ValidationContext
from polis.agents.actions.registry import ResolverRegistry
from polis.agents.actions.types import Action, ActionType, LegalAction
from polis.kernel.det import det_uuid

REFLEX_ALLOWED: Final[frozenset[ActionType]] = frozenset(
    {
        ActionType.MOVE_TO,
        ActionType.IDLE,
        ActionType.SLEEP,
        ActionType.EAT,
        ActionType.WORK,
        ActionType.STUDY,
        ActionType.BUY_GOOD,
        ActionType.SAY,
        ActionType.REPAY_LOAN,
        ActionType.NULL_ACTION,
    }
)

CHILD_ALLOWED: Final[frozenset[ActionType]] = frozenset(
    {
        ActionType.IDLE,
        ActionType.SLEEP,
        ActionType.EAT,
        ActionType.STUDY,
        ActionType.NULL_ACTION,
    }
)


def _life_stage_allows(state: object, action_type: ActionType) -> bool:
    stage = getattr(state, "stage", None)
    if stage is None:
        stage = getattr(state, "employment_status", None)
    if stage == "infant":
        return action_type == ActionType.NULL_ACTION
    if stage == "child":
        return action_type in CHILD_ALLOWED
    return True


def incarceration_allows(
    actor_id: str,
    action_type: ActionType,
    ctx: ValidationContext,
) -> bool:
    incarceration = ctx.repositories.get("incarceration")
    if incarceration is None:
        return True
    checker = getattr(incarceration, "is_incarcerated", None)
    if checker is None or not cast(Callable[[str, int], bool], checker)(actor_id, ctx.tick):
        return True
    allowed = cast(
        Collection[ActionType],
        getattr(incarceration, "ALLOWED_ACTIONS", ()),
    )
    return action_type in allowed


def legal_actions(
    obs: object,
    state: object,
    registry: ResolverRegistry,
    ctx: ValidationContext,
) -> tuple[LegalAction, ...]:
    del obs
    result: list[LegalAction] = []
    actor_id = str(getattr(state, "agent_id", "ag_probe"))
    for action_type in ActionType:
        resolver = registry.for_type(action_type)
        if (
            resolver is None
            or not _life_stage_allows(state, action_type)
            or not incarceration_allows(actor_id, action_type, ctx)
        ):
            continue
        probe = Action(
            action_id=det_uuid("polis.action.legal", actor_id, ctx.tick, action_type.value),
            actor_id=actor_id,
            tick=ctx.tick,
            type=action_type,
            params={},
            origin="scripted",
            salience=0,
        )
        if resolver.check_capability(probe, ctx) is not None:
            continue
        if resolver.check_locality(probe, ctx) is not None:
            continue
        result.append(
            LegalAction(
                type=action_type,
                param_schema=PARAMS_MODELS[action_type].model_json_schema(),
                options=resolver.options_for(action_type, ctx),
            )
        )
    return tuple(result)
