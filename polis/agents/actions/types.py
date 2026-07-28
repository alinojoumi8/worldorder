from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from polis.config.canon import canonical_json
from polis.kernel.det import det_uuid

if TYPE_CHECKING:
    from polis.agents.actions.params.base import ActionParams

ActionOrigin = Literal[
    "reflex",
    "deliberate",
    "reflect",
    "external",
    "scripted",
    "fallback",
]
Gate = Literal["schema", "capability", "locality", "resources", "legality"]
RejectReason = Literal[
    "schema",
    "capability",
    "locality",
    "resources",
    "unknown_type",
    "no_slots",
    "unavailable",
]


class ActionType(StrEnum):
    # world (5)
    MOVE_TO = "MOVE_TO"
    IDLE = "IDLE"
    SLEEP = "SLEEP"
    EAT = "EAT"
    RENT_HOME = "RENT_HOME"
    # speech (3)
    SAY = "SAY"
    DIRECT_MESSAGE = "DIRECT_MESSAGE"
    BROADCAST = "BROADCAST"
    # labour (9)
    APPLY_FOR_JOB = "APPLY_FOR_JOB"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    DECLINE_OFFER = "DECLINE_OFFER"
    QUIT_JOB = "QUIT_JOB"
    NEGOTIATE_WAGE = "NEGOTIATE_WAGE"
    POST_VACANCY = "POST_VACANCY"
    MAKE_OFFER = "MAKE_OFFER"
    FIRE_EMPLOYEE = "FIRE_EMPLOYEE"
    WORK = "WORK"
    # education (4)
    ENROL = "ENROL"
    STUDY = "STUDY"
    DROP_OUT = "DROP_OUT"
    TAKE_EXAM = "TAKE_EXAM"
    # goods (4)
    BUY_GOOD = "BUY_GOOD"
    SET_PRICE = "SET_PRICE"
    PRODUCE = "PRODUCE"
    RESTOCK = "RESTOCK"
    # exchange (4)
    SUBMIT_ORDER = "SUBMIT_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"
    SHORT = "SHORT"
    IPO_LIST = "IPO_LIST"
    # banking (6)
    OPEN_ACCOUNT = "OPEN_ACCOUNT"
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    APPLY_FOR_LOAN = "APPLY_FOR_LOAN"
    REPAY_LOAN = "REPAY_LOAN"
    DEFAULT = "DEFAULT"
    # ventures (8)
    FOUND_COMPANY = "FOUND_COMPANY"
    PITCH = "PITCH"
    ISSUE_TERM_SHEET = "ISSUE_TERM_SHEET"
    INVEST = "INVEST"
    ACQUIRE = "ACQUIRE"
    SELL_STAKE = "SELL_STAKE"
    FILE_BANKRUPTCY = "FILE_BANKRUPTCY"
    DECLARE_DIVIDEND = "DECLARE_DIVIDEND"
    # media (8)
    POST = "POST"
    REPOST = "REPOST"
    LIKE = "LIKE"
    COMMENT = "COMMENT"
    FOLLOW = "FOLLOW"
    UNFOLLOW = "UNFOLLOW"
    PUBLISH_ARTICLE = "PUBLISH_ARTICLE"
    RETRACT = "RETRACT"
    # polity (7)
    JOIN_PARTY = "JOIN_PARTY"
    ANNOUNCE_CANDIDACY = "ANNOUNCE_CANDIDACY"
    CAMPAIGN = "CAMPAIGN"
    VOTE = "VOTE"
    PROPOSE_POLICY = "PROPOSE_POLICY"
    LOBBY = "LOBBY"
    FOUND_PARTY = "FOUND_PARTY"
    # law (7)
    COMMIT_CRIME = "COMMIT_CRIME"
    REPORT_CRIME = "REPORT_CRIME"
    FILE_SUIT = "FILE_SUIT"
    RETAIN_COUNSEL = "RETAIN_COUNSEL"
    TESTIFY = "TESTIFY"
    SETTLE = "SETTLE"
    RULE = "RULE"
    # social (5)
    BEFRIEND = "BEFRIEND"
    COURT = "COURT"
    PROPOSE_UNION = "PROPOSE_UNION"
    DISSOLVE_UNION = "DISSOLVE_UNION"
    HAVE_CHILD_INTENT = "HAVE_CHILD_INTENT"
    # meta (1)
    NULL_ACTION = "NULL_ACTION"


@dataclass(frozen=True, slots=True)
class Action:
    action_id: UUID
    actor_id: str
    tick: int
    type: ActionType
    params: Mapping[str, Any]
    origin: ActionOrigin
    salience: float
    reasoning: str | None = ""
    speech: str | None = None
    sig: str | None = None


@dataclass(frozen=True, slots=True)
class LegalityVerdict:
    is_crime: bool
    crime_type: str | None = None
    victim_id: str | None = None
    amount_cents: int | None = None
    crime_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedAction:
    action: Action
    validated_params: ActionParams
    legality: LegalityVerdict
    slot_index: int


@dataclass(frozen=True, slots=True)
class Rejection:
    action_id: UUID
    actor_id: str
    type: ActionType
    gate: Gate | None
    reason: RejectReason
    detail: str
    substitute: Action


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    action_id: UUID
    tick: int
    type: ActionType
    status: Literal["applied", "rejected"]
    reason: RejectReason | None
    detail: str | None
    effects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateFailure:
    reason: RejectReason
    detail: str = ""


GateResult = GateFailure | None


@dataclass(frozen=True, slots=True)
class LegalAction:
    type: ActionType
    param_schema: Mapping[str, Any]
    options: tuple[Mapping[str, Any], ...]


def make_action(
    *,
    actor_id: str,
    tick: int,
    action_type: ActionType,
    params: Mapping[str, Any] | None = None,
    origin: ActionOrigin = "reflex",
    salience: float = 0,
    reasoning: str | None = "",
    speech: str | None = None,
    sig: str | None = None,
    ordinal: int = 0,
) -> Action:
    normalized_params = dict(params or {})
    action_id = det_uuid(
        "polis.action",
        actor_id,
        tick,
        ordinal,
        action_type.value,
        canonical_json(normalized_params),
    )
    return Action(
        action_id,
        actor_id,
        tick,
        action_type,
        normalized_params,
        origin,
        salience,
        reasoning,
        speech,
        sig,
    )


def make_legacy_action(
    *,
    actor_id: str,
    tick: int,
    action_type: ActionType,
    params: Mapping[str, Any] | None = None,
    origin: ActionOrigin = "reflex",
    salience: float = 0,
    reasoning: str | None = "",
    speech: str | None = None,
    sig: str | None = None,
    ordinal: int = 0,
) -> Action:
    """Preserve the M1-M3 action-id representation for frozen replay hashes."""

    normalized_params = dict(params or {})
    action_id = det_uuid(
        "polis.action",
        actor_id,
        tick,
        ordinal,
        action_type.value,
        normalized_params,
    )
    return Action(
        action_id,
        actor_id,
        tick,
        action_type,
        normalized_params,
        origin,
        salience,
        reasoning,
        speech,
        sig,
    )


def null_action(action: Action, *, reasoning: str) -> Action:
    return Action(
        action.action_id,
        action.actor_id,
        action.tick,
        ActionType.NULL_ACTION,
        {"replaced_type": action.type.value, "reason": reasoning},
        action.origin,
        action.salience,
        action.reasoning,
        action.speech,
        None,
    )
