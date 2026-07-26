from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from polis.kernel.det import det_uuid

ActionOrigin = Literal[
    "reflex",
    "deliberate",
    "reflect",
    "external",
    "scripted",
    "fallback",
]


class ActionType(StrEnum):
    MOVE_TO = "MOVE_TO"
    IDLE = "IDLE"
    SLEEP = "SLEEP"
    EAT = "EAT"
    APPLY_FOR_JOB = "APPLY_FOR_JOB"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    DECLINE_OFFER = "DECLINE_OFFER"
    QUIT_JOB = "QUIT_JOB"
    NEGOTIATE_WAGE = "NEGOTIATE_WAGE"
    POST_VACANCY = "POST_VACANCY"
    MAKE_OFFER = "MAKE_OFFER"
    FIRE_EMPLOYEE = "FIRE_EMPLOYEE"
    WORK = "WORK"
    STUDY = "STUDY"
    BUY_GOOD = "BUY_GOOD"
    SET_PRICE = "SET_PRICE"
    PRODUCE = "PRODUCE"
    RESTOCK = "RESTOCK"
    OPEN_ACCOUNT = "OPEN_ACCOUNT"
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    APPLY_FOR_LOAN = "APPLY_FOR_LOAN"
    REPAY_LOAN = "REPAY_LOAN"
    DEFAULT = "DEFAULT"
    NULL_ACTION = "NULL_ACTION"


@dataclass(frozen=True, slots=True)
class Action:
    action_id: UUID
    actor_id: str
    tick: int
    type: ActionType
    params: dict[str, Any]
    origin: ActionOrigin
    salience: float
    reasoning: str = ""
    speech: str | None = None


def make_action(
    *,
    actor_id: str,
    tick: int,
    action_type: ActionType,
    params: dict[str, Any] | None = None,
    origin: ActionOrigin = "reflex",
    salience: float = 0,
    reasoning: str = "",
    ordinal: int = 0,
) -> Action:
    action_id = det_uuid(
        "polis.action",
        actor_id,
        tick,
        ordinal,
        action_type.value,
        params or {},
    )
    return Action(
        action_id,
        actor_id,
        tick,
        action_type,
        params or {},
        origin,
        salience,
        reasoning,
    )


def null_action(action: Action, *, reasoning: str) -> Action:
    return Action(
        action.action_id,
        action.actor_id,
        action.tick,
        ActionType.NULL_ACTION,
        {},
        action.origin,
        action.salience,
        reasoning,
    )
