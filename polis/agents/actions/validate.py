from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class ApplyForJobParams(_Params):
    vacancy_id: str
    asked_wage_cents: int | None = Field(default=None, ge=0)


class OfferDecisionParams(_Params):
    offer_id: str
    reason: str | None = None


class NegotiateWageParams(_Params):
    offer_id: str | None = None
    employment_id: str | None = None
    counter_cents: int = Field(ge=0)


class EmploymentParams(_Params):
    employment_id: str
    reason: str | None = None


class PostVacancyParams(_Params):
    firm_id: str
    occupation: str
    wage_offer_cents: int = Field(ge=0)
    headcount: int = Field(ge=1)


class MakeOfferParams(_Params):
    application_id: str
    wage_cents: int = Field(ge=0)


class WorkParams(_Params):
    employment_id: str
    effort_bp: int = Field(default=10_000, ge=0, le=10_000)


class BuyGoodParams(_Params):
    sku: str
    qty: int = Field(ge=1, le=1_000)
    seller_firm_id: str
    max_unit_price_cents: int = Field(ge=0)


class SetPriceParams(_Params):
    firm_id: str
    sku: str
    price_cents: int = Field(ge=1)


class ProduceParams(_Params):
    sku: str


class RestockParams(_Params):
    sku: str
    qty: int = Field(ge=1)


class AccountParams(_Params):
    bank_id: str
    amount_cents: int | None = Field(default=None, ge=0)


class LoanParams(_Params):
    loan_id: str | None = None
    bank_id: str | None = None
    amount_cents: int = Field(ge=1)


class SubmitOrderParams(_Params):
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"] = "limit"
    qty: int = Field(ge=1)
    limit_price_cents: int | None = Field(default=None, ge=1)
    flags: tuple[str, ...] = ()


class CancelOrderParams(_Params):
    order_id: str


class ShortParams(_Params):
    symbol: str
    qty: int = Field(ge=1)
    limit_price_cents: int = Field(ge=1)
    collateral_cents: int = Field(ge=1)


class IpoListParams(_Params):
    firm_id: str
    symbol: str
    shares_offered: int = Field(ge=1)
    primary_shares: int = Field(ge=0)
    secondary_shares: int = Field(ge=0)
    price_low_cents: int = Field(ge=1)
    price_high_cents: int = Field(ge=1)
    underwriter_bank_id: str


class FoundCompanyParams(_Params):
    name: str
    sector: str
    place_id: str
    initial_capital_cents: int = Field(ge=1)
    is_startup: bool = False
    is_fund: bool = False
    thesis: str = ""


class PitchParams(_Params):
    startup_id: str
    investor_id: str
    ask_cents: int = Field(ge=1)
    pre_money_ask_cents: int = Field(ge=1)
    deck_text: str


class TermSheetParams(_Params):
    startup_id: str
    investor_id: str
    pre_money_cents: int = Field(ge=1)
    amount_cents: int = Field(ge=1)
    security: Literal["common", "preferred"] = "preferred"
    liq_pref_bp: int = Field(default=10_000, ge=0)
    participating: bool = False
    pro_rata: bool = True
    board_seat: bool = False
    option_pool_bp: int = Field(default=1_000, ge=0, lt=10_000)
    anti_dilution: Literal["none", "broad_weighted", "full_ratchet"] = "broad_weighted"


class InvestParams(_Params):
    target_id: str
    cents: int = Field(ge=1)
    instrument: Literal["round", "lp_commitment", "bond"] = "round"
    term_sheet_id: str | None = None


class AcquireParams(_Params):
    acquirer_id: str
    target_id: str
    offer_cents: int = Field(ge=1)
    consideration: Literal["cash", "stock", "mixed"] = "cash"
    stock_ratio_bp: int = Field(default=0, ge=0, le=10_000)
    integration_mode: Literal["absorb", "standalone", "asset_sale"] = "absorb"
    financing: str = "cash"


class SellStakeParams(_Params):
    firm_id: str
    qty: int = Field(ge=1)
    price_cents: int | None = Field(default=None, ge=1)
    deal_id: str | None = None


class BankruptcyParams(_Params):
    entity_id: str | None = None
    reason: str = "voluntary"


class DividendParams(_Params):
    firm_id: str
    total_cents: int = Field(ge=1)


_PARAM_MODELS: dict[ActionType, type[_Params]] = {
    ActionType.MOVE_TO: MoveParams,
    ActionType.IDLE: EmptyParams,
    ActionType.SLEEP: EmptyParams,
    ActionType.EAT: EmptyParams,
    ActionType.APPLY_FOR_JOB: ApplyForJobParams,
    ActionType.ACCEPT_OFFER: OfferDecisionParams,
    ActionType.DECLINE_OFFER: OfferDecisionParams,
    ActionType.QUIT_JOB: EmploymentParams,
    ActionType.NEGOTIATE_WAGE: NegotiateWageParams,
    ActionType.POST_VACANCY: PostVacancyParams,
    ActionType.MAKE_OFFER: MakeOfferParams,
    ActionType.FIRE_EMPLOYEE: EmploymentParams,
    ActionType.WORK: WorkParams,
    ActionType.STUDY: EmptyParams,
    ActionType.BUY_GOOD: BuyGoodParams,
    ActionType.SET_PRICE: SetPriceParams,
    ActionType.PRODUCE: ProduceParams,
    ActionType.RESTOCK: RestockParams,
    ActionType.OPEN_ACCOUNT: AccountParams,
    ActionType.DEPOSIT: AccountParams,
    ActionType.WITHDRAW: AccountParams,
    ActionType.APPLY_FOR_LOAN: LoanParams,
    ActionType.REPAY_LOAN: LoanParams,
    ActionType.DEFAULT: LoanParams,
    ActionType.SUBMIT_ORDER: SubmitOrderParams,
    ActionType.CANCEL_ORDER: CancelOrderParams,
    ActionType.SHORT: ShortParams,
    ActionType.IPO_LIST: IpoListParams,
    ActionType.FOUND_COMPANY: FoundCompanyParams,
    ActionType.PITCH: PitchParams,
    ActionType.ISSUE_TERM_SHEET: TermSheetParams,
    ActionType.INVEST: InvestParams,
    ActionType.ACQUIRE: AcquireParams,
    ActionType.SELL_STAKE: SellStakeParams,
    ActionType.FILE_BANKRUPTCY: BankruptcyParams,
    ActionType.DECLARE_DIVIDEND: DividendParams,
    ActionType.NULL_ACTION: EmptyParams,
}


def action_response_schema(legal_actions: Sequence[str]) -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    seen: set[ActionType] = set()
    for value in legal_actions:
        try:
            action_type = ActionType(value)
        except ValueError:
            continue
        if action_type in seen:
            continue
        seen.add(action_type)
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"const": action_type.value},
                    "params": _PARAM_MODELS[action_type].model_json_schema(),
                },
                "required": ["type", "params"],
            }
        )
    if not branches:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"const": ActionType.NULL_ACTION.value},
                    "params": EmptyParams.model_json_schema(),
                },
                "required": ["type", "params"],
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reasoning": {"type": "string", "maxLength": 300},
            "action": {"oneOf": branches},
        },
        "required": ["reasoning", "action"],
    }


_ECONOMIC_ACTIONS = frozenset(
    {
        ActionType.APPLY_FOR_JOB,
        ActionType.ACCEPT_OFFER,
        ActionType.DECLINE_OFFER,
        ActionType.QUIT_JOB,
        ActionType.NEGOTIATE_WAGE,
        ActionType.POST_VACANCY,
        ActionType.MAKE_OFFER,
        ActionType.FIRE_EMPLOYEE,
        ActionType.WORK,
        ActionType.BUY_GOOD,
        ActionType.SET_PRICE,
        ActionType.PRODUCE,
        ActionType.RESTOCK,
        ActionType.OPEN_ACCOUNT,
        ActionType.DEPOSIT,
        ActionType.WITHDRAW,
        ActionType.APPLY_FOR_LOAN,
        ActionType.REPAY_LOAN,
        ActionType.DEFAULT,
        ActionType.SUBMIT_ORDER,
        ActionType.CANCEL_ORDER,
        ActionType.SHORT,
        ActionType.IPO_LIST,
        ActionType.FOUND_COMPANY,
        ActionType.PITCH,
        ActionType.ISSUE_TERM_SHEET,
        ActionType.INVEST,
        ActionType.ACQUIRE,
        ActionType.SELL_STAKE,
        ActionType.FILE_BANKRUPTCY,
        ActionType.DECLARE_DIVIDEND,
    }
)


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
    elif action.type not in _ECONOMIC_ACTIONS and not world.affords(
        location.place_id, action.type.value
    ):
        gates["locality"] = "fail"
        return _reject(action, "locality", gates)
    gates["locality"] = "pass"

    if not budget.consume(action.actor_id):
        gates["resources"] = "fail"
        return _reject(action, "resources", gates, {"reason": "action_slots"})
    gates["resources"] = "pass"
    gates["legality"] = "clean"
    return Validation(True, action, None, gates, {})
