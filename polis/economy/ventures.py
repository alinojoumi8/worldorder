from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

from polis.agents.actions.types import Action, ActionType, make_action
from polis.agents.state import AgentPopulation
from polis.config.canon import canonical_json
from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.credit import CreditContext, write_off_loan
from polis.economy.exchange.engine import ExchangeEngine
from polis.economy.labour import redundancy_order
from polis.economy.ledger import LedgerError, Leg, bank_of, parse_account_id
from polis.economy.money import allocate, bp
from polis.economy.state import EconomyState, FirmState, InventoryState
from polis.economy.venture_state import (
    AcquisitionState,
    BankruptcyCaseState,
    CapTableState,
    ClaimState,
    FundingRoundState,
    PitchState,
    StartupState,
    TermSheetState,
    VCFundState,
)
from polis.events.kinds import (
    ACCOUNT_OPENED,
    ACQUISITION_APPROVED,
    ACQUISITION_COMPLETED,
    ACQUISITION_PROPOSED,
    ASSET_SALE,
    ASSETS_LIQUIDATED,
    AUTOMATIC_STAY_IMPOSED,
    BANKRUPTCY_DISCHARGED,
    BANKRUPTCY_FILED,
    CAP_TABLE_UPDATED,
    CAPITAL_CALLED,
    CLAIM_REGISTERED,
    CREDIT_FLAG_SET,
    DISTRIBUTION_MADE,
    DIVIDEND_DECLARED,
    DIVIDEND_PAID,
    DOWN_ROUND,
    EXEMPTION_APPLIED,
    EXIT_COMPLETED,
    FIRED,
    FIRM_FOUNDED,
    FUND_DISTRIBUTION,
    INTEGRATION_COMPLETED,
    INVENTORY_WRITTEN_OFF,
    MANAGEMENT_FEE_CHARGED,
    OFFER_EXPIRED,
    OPTION_POOL_SET,
    PITCH_EVALUATED,
    PITCH_MADE,
    ROUND_CLOSED,
    RUNWAY_UPDATED,
    STARTUP_DIED,
    STARTUP_FOUNDED,
    TERM_SHEET_ACCEPTED,
    TERM_SHEET_EXPIRED,
    TERM_SHEET_ISSUED,
    VACANCY_CLOSED,
    VC_FUND_FORMED,
    WATERFALL_APPLIED,
)
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.llm.purposes import Purpose
from polis.llm.router import LLMRouter

Emit = Callable[[NewEvent], Event]

VC_EVAL_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "conviction_bp",
        "thesis_fit_bp",
        "valuation_view_cents",
        "check_size_cents",
        "verdict",
        "concerns",
    ],
    "properties": {
        "conviction_bp": {"type": "integer", "minimum": 0, "maximum": 10_000},
        "thesis_fit_bp": {"type": "integer", "minimum": 0, "maximum": 10_000},
        "valuation_view_cents": {"type": "integer", "minimum": 1},
        "check_size_cents": {"type": "integer", "minimum": 0},
        "verdict": {"type": "string", "enum": ["pass", "explore", "term_sheet"]},
        "concerns": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 6,
        },
    },
}


def _coalesce(legs: Iterable[Leg]) -> list[Leg]:
    totals: dict[tuple[str, int, str], int] = defaultdict(int)
    for leg in legs:
        totals[(leg.account_id, leg.direction, leg.reason)] += leg.amount_cents
    return [
        Leg(account, direction, cents, reason)
        for (account, direction, reason), cents in sorted(totals.items())
        if cents > 0
    ]


@mechanism(
    "venture_valuation",
    entails=(
        "Valuations are anchored to the median of recent comparable rounds and to a revenue "
        "multiple. Valuation momentum — a rise in recent valuations mechanically raising the "
        "next valuation — therefore follows in part from the anchoring rule, and a "
        "private-market bubble is partly implied. Set w_llm_bp: 10,000 for the ablation in "
        "which valuation is entirely investor-determined; any A3 or A6 claim about venture "
        "valuations must report both settings."
    ),
    config_key="mechanisms.venture_valuation",
)
def venture_pre_money_cents(
    anchor_cents: int,
    valuation_view_cents: int,
    llm_weight_bp: int,
) -> int:
    if anchor_cents < 0 or valuation_view_cents < 0:
        raise ValueError("venture valuations cannot be negative")
    if not 0 <= llm_weight_bp <= 10_000:
        raise ValueError("venture valuation weight must be within 0..10000")
    return (
        (10_000 - llm_weight_bp) * anchor_cents + llm_weight_bp * valuation_view_cents
    ) // 10_000


@mechanism(
    "ma.valuation_anchor",
    entails=(
        "Offer prices are anchored at or above the market capitalisation of listed targets "
        "and above a DCF/comparables blend for private ones. A positive acquisition premium "
        "is therefore implied. Not implied: the level of premiums, their cyclicality, whether "
        "acquirers overpay relative to realised synergies, or the post-acquisition "
        "productivity path."
    ),
    config_key="mechanisms.ma_valuation_anchor",
)
def acquisition_offer_cents(anchor_cents: int, premium_bp: int) -> int:
    if anchor_cents < 0:
        raise ValueError("acquisition anchor cannot be negative")
    if premium_bp < -10_000:
        raise ValueError("acquisition premium cannot reduce value below zero")
    return anchor_cents * (10_000 + premium_bp) // 10_000


@mechanism(
    "ventures.integration_synergy",
    entails=(
        "Setting integration_synergy_bp = 0 by default matters: assuming positive synergies "
        'would make "acquisitions improve productivity" a mechanism, and A6 asks whether '
        "they do."
    ),
    config_key="mechanisms.ventures_integration_synergy",
)
def integrated_productivity_bp(
    acquirer_productivity_bp: int,
    acquirer_capital_cents: int,
    target_productivity_bp: int,
    target_capital_cents: int,
    integration_delta_bp: int,
) -> tuple[int, int]:
    total_capital = acquirer_capital_cents + target_capital_cents
    if total_capital <= 0:
        return acquirer_productivity_bp, 0
    blended = (
        acquirer_productivity_bp * acquirer_capital_cents
        + target_productivity_bp * target_capital_cents
    ) // total_capital
    integrated = max(1, blended + integration_delta_bp)
    return integrated, integrated - blended


def venture_waterfall(
    proceeds_cents: int,
    cap_rows: Sequence[CapTableState],
    rounds: Sequence[FundingRoundState],
) -> dict[str, int]:
    """Allocate acquisition/liquidation proceeds exactly and deterministically."""
    if proceeds_cents < 0:
        raise ValueError("waterfall proceeds cannot be negative")
    if proceeds_cents == 0:
        return {}
    shares_by_holder: dict[str, int] = defaultdict(int)
    for row in cap_rows:
        if row.shares > 0:
            shares_by_holder[row.holder_id] += row.shares
    if not shares_by_holder:
        raise ValueError("waterfall needs at least one positive shareholding")
    remaining = proceeds_cents
    result: dict[str, int] = defaultdict(int)
    participating_holders: set[str] = set()
    for round_row in sorted(rounds, key=lambda row: (-row.closed_tick, row.round_id)):
        round_cap = [row for row in cap_rows if row.round_id == round_row.round_id]
        if not round_cap:
            continue
        pref = round_row.amount_cents * round_row.liq_pref_bp // 10_000
        paid = min(remaining, pref)
        weights = [(row.holder_id, row.shares) for row in round_cap if row.shares > 0]
        for holder_id, cents in allocate(paid, weights).items():
            result[holder_id] += cents
        remaining -= paid
        if round_row.participating:
            participating_holders.update(row.holder_id for row in round_cap)
        if remaining == 0:
            break
    residual_weights = [
        (holder_id, shares)
        for holder_id, shares in sorted(shares_by_holder.items())
        if any(
            row.holder_id == holder_id
            and (row.share_class == "common" or holder_id in participating_holders)
            for row in cap_rows
        )
    ]
    if remaining and not residual_weights:
        residual_weights = list(sorted(shares_by_holder.items()))
    for holder_id, cents in allocate(remaining, residual_weights).items():
        result[holder_id] += cents
    if sum(result.values()) != proceeds_cents:
        raise RuntimeError("venture waterfall did not allocate exact proceeds")
    return {holder_id: cents for holder_id, cents in sorted(result.items()) if cents > 0}


def _monotone_pro_rata(
    pool_cents: int,
    weighted_claims: Sequence[tuple[str, int]],
) -> dict[str, int]:
    """Use capped highest averages so a larger pool never reduces a recovery."""
    weights = dict(weighted_claims)
    if len(weights) != len(weighted_claims):
        raise ValueError("pro-rata allocation ids must be unique")
    if pool_cents < 0 or any(weight < 0 for weight in weights.values()):
        raise ValueError("pro-rata values cannot be negative")
    total_weight = sum(weights.values())
    if pool_cents > total_weight:
        raise ValueError("pro-rata pool cannot exceed total claims")
    if pool_cents == 0:
        return dict.fromkeys(sorted(weights), 0)
    if pool_cents == total_weight:
        return dict(sorted(weights.items()))

    # A Jefferson/D'Hondt highest-averages allocation is house-monotone. Locate
    # its cutoff divisor in logarithmic time, then resolve only the tied seats
    # using exact rational comparisons and stable claim-id ordering.
    max_weight = max(weights.values(), default=0)
    precision = max(80, len(str(max_weight)) * 3 + 32)
    with localcontext() as context:
        context.prec = precision
        low = Decimal(0)
        high = Decimal(max_weight + 1)
        for _ in range(precision * 4):
            midpoint = (low + high) / 2
            if midpoint in (low, high):
                break
            seats = sum(min(weight, int(Decimal(weight) / midpoint)) for weight in weights.values())
            if seats >= pool_cents:
                low = midpoint
            else:
                high = midpoint
        payments = {
            claim_id: min(weight, int(Decimal(weight) / high))
            for claim_id, weight in weights.items()
        }

    remaining = pool_cents - sum(payments.values())
    if remaining < 0:
        raise RuntimeError("pro-rata divisor search over-allocated the pool")
    while remaining:
        candidates = [
            (claim_id, weight)
            for claim_id, weight in weights.items()
            if payments[claim_id] < weight
        ]
        if not candidates:
            raise RuntimeError("pro-rata allocation exhausted all claims")
        claim_id, _ = min(
            candidates,
            key=lambda item: (
                -Fraction(item[1], payments[item[0]] + 1),
                item[0],
            ),
        )
        payments[claim_id] += 1
        remaining -= 1
    return dict(sorted(payments.items()))


def priority_waterfall(
    proceeds_cents: int,
    claims: Sequence[ClaimState],
) -> dict[str, int]:
    """Allocate a bankruptcy estate by strict class priority and class pro rata."""
    if proceeds_cents < 0:
        raise ValueError("bankruptcy proceeds cannot be negative")
    if len({claim.claim_id for claim in claims}) != len(claims):
        raise ValueError("bankruptcy claim ids must be unique")
    if any(claim.claim_cents < 0 for claim in claims):
        raise ValueError("bankruptcy claims cannot be negative")
    if any(not 1 <= claim.priority_class <= 5 for claim in claims):
        raise ValueError("bankruptcy priority class must be within 1..5")
    payments = {claim.claim_id: 0 for claim in claims}
    remaining = proceeds_cents
    for priority in range(1, 6):
        class_claims = sorted(
            (
                claim
                for claim in claims
                if claim.priority_class == priority and claim.claim_cents > 0
            ),
            key=lambda claim: claim.claim_id,
        )
        class_total = sum(claim.claim_cents for claim in class_claims)
        pool = min(remaining, class_total)
        if pool:
            payments.update(
                _monotone_pro_rata(
                    pool,
                    [(claim.claim_id, claim.claim_cents) for claim in class_claims],
                )
            )
        remaining -= pool
        if remaining == 0:
            break
    total_claimed = sum(claim.claim_cents for claim in claims)
    if sum(payments.values()) != min(proceeds_cents, total_claimed):
        raise RuntimeError("priority waterfall did not allocate exact proceeds")
    for junior in claims:
        if payments[junior.claim_id] <= 0:
            continue
        if not all(
            payments[senior.claim_id] == senior.claim_cents
            for senior in claims
            if senior.priority_class < junior.priority_class
        ):
            raise RuntimeError("junior claim paid before senior claims were satisfied")
    return dict(sorted(payments.items()))


class VentureEngine:
    def __init__(
        self,
        settings: Settings,
        population: AgentPopulation,
        economy: EconomyState,
        rng: RngRegistry,
        router: LLMRouter,
        exchange: ExchangeEngine,
        credit_context: CreditContext,
    ) -> None:
        self.settings = settings
        self.population = population
        self.economy = economy
        self.rng = rng
        self.router = router
        self.exchange = exchange
        self.credit_context = credit_context

    async def resolve(
        self,
        actions: Sequence[Action],
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        if not (self.settings.ventures.enabled or self.settings.bankruptcy.enabled):
            return ()
        events: list[Event] = []
        events.extend(self._expire_term_sheets(tick, emit))
        venture_actions = [
            action
            for action in actions
            if action.type
            in {
                ActionType.FOUND_COMPANY,
                ActionType.PITCH,
                ActionType.ISSUE_TERM_SHEET,
                ActionType.INVEST,
                ActionType.ACQUIRE,
                ActionType.SELL_STAKE,
                ActionType.FILE_BANKRUPTCY,
                ActionType.DECLARE_DIVIDEND,
            }
        ]
        for action in sorted(venture_actions, key=lambda row: (row.actor_id, str(row.action_id))):
            if action.type != ActionType.FILE_BANKRUPTCY and self._action_under_stay(action):
                continue
            if action.type == ActionType.FOUND_COMPANY:
                events.extend(self._found_company(action, tick, emit))
            elif action.type == ActionType.PITCH:
                events.extend(await self._pitch(action, tick, emit))
            elif action.type == ActionType.ISSUE_TERM_SHEET:
                events.extend(self._issue_term_sheet(action, tick, emit))
            elif action.type == ActionType.INVEST:
                events.extend(self._invest(action, tick, emit))
            elif action.type == ActionType.ACQUIRE:
                events.extend(self._propose_acquisition(action, tick, emit))
            elif action.type == ActionType.SELL_STAKE:
                events.extend(self._tender(action, tick, emit))
            elif action.type == ActionType.FILE_BANKRUPTCY:
                events.extend(self._file_action(action, tick, emit))
            elif action.type == ActionType.DECLARE_DIVIDEND:
                events.extend(self._declare_dividend(action, tick, emit))
        events.extend(self._runway_step(tick, emit))
        events.extend(self._solvency_step(tick, emit))
        events.extend(self._advance_bankruptcies(tick, emit))
        events.extend(self._fund_step(tick, emit))
        return tuple(events)

    def liquidation_actions(self, tick: int) -> tuple[Action, ...]:
        """Create deterministic institutional market sells before the exchange resolves."""
        if not (self.settings.exchange.enabled and self.settings.bankruptcy.enabled):
            return ()
        ticks_per_day = self.settings.clock.ticks_per_sim_day
        if self.settings.clock.profile == "microscope" and tick % ticks_per_day != 9:
            return ()
        actions: list[Action] = []
        for case in sorted(
            self.economy.ventures.bankruptcies.values(),
            key=lambda row: row.case_id,
        ):
            if (
                case.status != "open"
                or case.liquidation_tick is None
                or tick <= case.filed_tick
                or tick > case.liquidation_tick
            ):
                continue
            sessions_left = max(
                1,
                (case.liquidation_tick - tick) // ticks_per_day + 1,
            )
            for symbol, security in sorted(self.economy.exchange.securities.items()):
                if security.status != "listed":
                    continue
                holding = self.economy.exchange.holdings.get(
                    self.economy.exchange.holding_key(case.entity_id, symbol)
                )
                if holding is None:
                    continue
                available = max(0, holding.qty - holding.reserved_qty)
                if available == 0:
                    continue
                if sessions_left == 1:
                    target = available
                else:
                    even_slice = max(1, (available + sessions_left - 1) // sessions_left)
                    jitter_bp = self.rng.get(
                        "exchange.liquidation",
                        f"{case.case_id}:{symbol}",
                        tick,
                    ).randint(7_500, 12_500)
                    target = min(available, max(1, bp(even_slice, jitter_bp)))
                max_order_qty = max(
                    1,
                    security.shares_outstanding * self.settings.exchange.max_order_qty_bp // 10_000,
                )
                remaining = target
                ordinal = 0
                while remaining:
                    qty = min(remaining, max_order_qty)
                    actions.append(
                        make_action(
                            actor_id=case.entity_id,
                            tick=tick,
                            action_type=ActionType.SUBMIT_ORDER,
                            params={
                                "symbol": symbol,
                                "side": "sell",
                                "order_type": "market",
                                "qty": qty,
                                "flags": [
                                    "forced_liquidation",
                                    f"bankruptcy:{case.case_id}",
                                ],
                            },
                            origin="reflex",
                            reasoning="institutional bankruptcy liquidation",
                            ordinal=ordinal,
                        )
                    )
                    remaining -= qty
                    ordinal += 1
        return tuple(actions)

    def _found_company(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.settings.ventures.enabled:
            return ()
        params = action.params
        initial = int(params.get("initial_capital_cents", 0))
        if initial <= 0:
            return ()
        try:
            founder_deposit = self._deposit(action.actor_id)
        except LedgerError:
            return ()
        if self.economy.ledger.balance(founder_deposit) < initial:
            return ()
        firm_id = f"fm_{str(action.action_id).replace('-', '')[:16]}"
        bank_id = bank_of(founder_deposit)
        if bank_id is None:
            return ()
        firm_deposit = self.economy.ledger.open_account(
            "dep",
            firm_id,
            "firm",
            bank_id=bank_id,
            tick=tick,
        )
        account_event = emit(
            NewEvent(
                ACCOUNT_OPENED,
                {
                    "account_id": firm_deposit,
                    "owner_id": firm_id,
                    "owner_type": "firm",
                    "bank_id": bank_id,
                    "account_type": "deposit",
                    "code": "dep",
                },
                subject_ids=(firm_id,),
            )
        )
        firm = FirmState(
            firm_id=firm_id,
            name=str(params.get("name", "New Polis Company")),
            sector=str(params.get("sector", "services")),
            place_id=str(params.get("place_id", "")),
            founder_id=action.actor_id,
            ledger_account_id=firm_deposit,
            productivity_bp=10_000,
            capital_cents=self.settings.firms.capital_ref_cents,
        )
        self.economy.firms[firm_id] = firm
        expected_txn = self.economy.ledger.next_txn_id(tick)
        founded = emit(
            NewEvent(
                FIRM_FOUNDED,
                {
                    "firm_id": firm_id,
                    "founder_id": action.actor_id,
                    "name": firm.name,
                    "sector": firm.sector,
                    "place_id": firm.place_id,
                    "initial_capital_cents": initial,
                    "ledger_account_id": firm_deposit,
                    "is_startup": bool(params.get("is_startup", False)),
                    "registration_fee_cents": 0,
                },
                actor_id=action.actor_id,
                subject_ids=(firm_id,),
            )
        )
        txn_id = self.economy.ledger.post_transaction(
            self.economy.ledger.transfer(
                founder_deposit,
                firm_deposit,
                initial,
                "trade",
            ),
            tick=tick,
            cause=founded,
        )
        if txn_id != expected_txn:
            raise RuntimeError("company formation ledger ordinal diverged")
        cap = CapTableState(
            firm_id=firm_id,
            holder_id=action.actor_id,
            share_class="common",
            shares=self.settings.ventures.founder_shares,
            invested_cents=initial,
            conversion_price_cents=max(
                1,
                initial // self.settings.ventures.founder_shares,
            ),
        )
        self.economy.ventures.cap_table[
            self.economy.ventures.cap_key(firm_id, action.actor_id, "common")
        ] = cap
        events: list[Event] = [account_event, founded]
        events.append(
            self._cap_event(
                cap,
                before=0,
                cause="formation",
                emit=emit,
            )
        )
        if bool(params.get("is_fund", False)):
            fund_id = f"vf_{firm_id[3:]}"
            fund = VCFundState(
                fund_id=fund_id,
                firm_id=firm_id,
                gp_agent_id=action.actor_id,
                committed_cents=initial,
                called_cents=initial,
                deployed_cents=0,
                vintage_tick=tick,
                thesis=str(params.get("thesis", "")),
                management_fee_bp=self.settings.ventures.management_fee_bp,
                carry_bp=self.settings.ventures.carry_bp,
                hurdle_bp=self.settings.ventures.hurdle_bp,
                lps={action.actor_id: initial},
            )
            self.economy.ventures.funds[fund_id] = fund
            events.append(
                emit(
                    NewEvent(
                        VC_FUND_FORMED,
                        {
                            "fund_id": fund_id,
                            "firm_id": firm_id,
                            "gp_agent_id": action.actor_id,
                            "committed_cents": initial,
                            "lps": dict(fund.lps),
                            "vintage_tick": tick,
                            "thesis": fund.thesis,
                            "mgmt_fee_bp": fund.management_fee_bp,
                            "carry_bp": fund.carry_bp,
                            "hurdle_bp": fund.hurdle_bp,
                        },
                        actor_id=action.actor_id,
                        subject_ids=(firm_id,),
                    )
                )
            )
        elif bool(params.get("is_startup", False)):
            startup_id = f"st_{firm_id[3:]}"
            burn = max(1, initial // max(1, self.settings.ventures.fundraise_trigger_days))
            startup = StartupState(
                startup_id=startup_id,
                firm_id=firm_id,
                founder_id=action.actor_id,
                thesis=str(params.get("thesis", "")),
                sector=firm.sector,
                founded_tick=tick,
                initial_capital_cents=initial,
                burn_rate_cents=burn,
                runway_ticks=initial // burn,
            )
            self.economy.ventures.startups[startup_id] = startup
            events.append(
                emit(
                    NewEvent(
                        STARTUP_FOUNDED,
                        {
                            "startup_id": startup_id,
                            "firm_id": firm_id,
                            "founder_id": action.actor_id,
                            "thesis": startup.thesis,
                            "sector": startup.sector,
                            "initial_capital_cents": initial,
                            "burn_rate_cents": burn,
                        },
                        actor_id=action.actor_id,
                        subject_ids=(firm_id,),
                    )
                )
            )
        return tuple(events)

    async def _pitch(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.settings.ventures.enabled:
            return ()
        startup_id = str(action.params.get("startup_id", ""))
        startup = self.economy.ventures.startups.get(startup_id)
        investor_id = str(action.params.get("investor_id", ""))
        if (
            startup is None
            or startup.founder_id != action.actor_id
            or startup.status != "active"
            or sum(
                row.status == "open"
                for row in self.economy.ventures.pitches.values()
                if row.startup_id == startup_id
            )
            >= self.settings.ventures.max_open_pitches
        ):
            return ()
        pitch_id = f"pt_{str(action.action_id).replace('-', '')[:18]}"
        pitch = PitchState(
            pitch_id=pitch_id,
            startup_id=startup_id,
            founder_id=action.actor_id,
            investor_id=investor_id,
            ask_cents=int(action.params["ask_cents"]),
            pre_money_ask_cents=int(action.params["pre_money_ask_cents"]),
            deck_text=str(action.params.get("deck_text", "")),
            made_tick=tick,
        )
        self.economy.ventures.pitches[pitch_id] = pitch
        traction = self._traction(startup)
        made = emit(
            NewEvent(
                PITCH_MADE,
                {
                    "pitch_id": pitch_id,
                    "startup_id": startup_id,
                    "founder_id": action.actor_id,
                    "investor_id": investor_id,
                    "ask_cents": pitch.ask_cents,
                    "pre_money_ask_cents": pitch.pre_money_ask_cents,
                    "deck_text": pitch.deck_text,
                    "traction": traction,
                },
                actor_id=action.actor_id,
                subject_ids=(investor_id, startup.firm_id),
            )
        )
        evaluation, llm_call_id = await self._evaluate_pitch(pitch, startup, traction, tick)
        pitch.conviction_bp = int(evaluation["conviction_bp"])
        pitch.valuation_view_cents = int(evaluation["valuation_view_cents"])
        pitch.verdict = str(evaluation["verdict"])
        pitch.status = "evaluated"
        evaluated = emit(
            NewEvent(
                PITCH_EVALUATED,
                {
                    "pitch_id": pitch_id,
                    "investor_id": investor_id,
                    "conviction_bp": pitch.conviction_bp,
                    "thesis_fit_bp": int(evaluation["thesis_fit_bp"]),
                    "valuation_view_cents": pitch.valuation_view_cents,
                    "check_size_cents": int(evaluation["check_size_cents"]),
                    "verdict": pitch.verdict,
                    "concerns": list(evaluation["concerns"]),
                    "llm_call_id": llm_call_id,
                },
                actor_id=investor_id,
                subject_ids=(startup.firm_id,),
            )
        )
        return made, evaluated

    async def _evaluate_pitch(
        self,
        pitch: PitchState,
        startup: StartupState,
        traction: Mapping[str, Any],
        tick: int,
    ) -> tuple[Mapping[str, Any], str | None]:
        fallback = {
            "conviction_bp": 5_000,
            "thesis_fit_bp": 5_000,
            "valuation_view_cents": pitch.pre_money_ask_cents,
            "check_size_cents": min(pitch.ask_cents, self.economy.ledger.liquid(pitch.investor_id)),
            "verdict": "explore",
            "concerns": ["mechanical fallback"],
        }
        if Purpose.VC_EVAL.value not in self.settings.llm.routing:
            return fallback, None
        cap_table = [
            {
                "holder_id": row.holder_id,
                "class": row.share_class,
                "shares": row.shares,
            }
            for row in self._cap_rows(startup.firm_id)
        ]
        prompt = canonical_json(
            {
                "instruction": (
                    "Evaluate this venture using only supplied simulation state. "
                    "Return the structured decision."
                ),
                "startup": {
                    "startup_id": startup.startup_id,
                    "sector": startup.sector,
                    "thesis": startup.thesis,
                    "traction": dict(traction),
                    "cap_table": cap_table,
                },
                "ask": {
                    "amount_cents": pitch.ask_cents,
                    "pre_money_cents": pitch.pre_money_ask_cents,
                    "deck_text": pitch.deck_text,
                },
            }
        )
        result = await self.router.call(
            Purpose.VC_EVAL,
            pitch.investor_id,
            tick,
            {
                "system": "You are a venture investor operating inside a simulation.",
                "prompt": prompt,
            },
            VC_EVAL_SCHEMA,
        )
        if result.degraded or not result.parsed_ok or result.parsed is None:
            return fallback, str(result.call_id)
        return result.parsed, str(result.call_id)

    def _venture_valuation_anchor(self, startup: StartupState, tick: int) -> int:
        if startup.revenue_ttm_cents > 0:
            multiple = self.settings.ventures.sector_multiple_bp.get(
                startup.sector,
                self.settings.ventures.sector_multiple_bp.get("default", 10_000),
            )
            return max(
                1,
                startup.revenue_ttm_cents * multiple // 10_000,
            )
        comparable_stage = self._next_stage(startup.stage)
        comparable_rounds = [
            row
            for row in sorted(
                self.economy.ventures.rounds.values(),
                key=lambda item: (-item.closed_tick, item.round_id),
            )
            if row.startup_id != startup.startup_id
            and row.stage == comparable_stage
            and (other := self.economy.ventures.startups.get(row.startup_id)) is not None
            and other.sector == startup.sector
        ]
        window = self.settings.ventures.comparable_window
        if len(comparable_rounds) > window:
            self.rng.get("ventures.comparables", startup.startup_id, tick).shuffle(
                comparable_rounds
            )
            comparable_rounds = comparable_rounds[:window]
        values = sorted(row.pre_money_cents for row in comparable_rounds)
        if not values:
            return max(1, self.settings.ventures.seed_default_pre_money_cents)
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) // 2

    def _issue_term_sheet(
        self,
        action: Action,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        if not self.settings.ventures.enabled:
            return ()
        startup_id = str(action.params.get("startup_id", ""))
        startup = self.economy.ventures.startups.get(startup_id)
        investor_id = str(action.params.get("investor_id", action.actor_id))
        if startup is None or investor_id != action.actor_id:
            return ()
        qualified = any(
            row.startup_id == startup_id
            and row.investor_id == investor_id
            and row.verdict == "term_sheet"
            for row in self.economy.ventures.pitches.values()
        )
        if not qualified and any(
            row.startup_id == startup_id and row.investor_id == investor_id
            for row in self.economy.ventures.pitches.values()
        ):
            return ()
        params = action.params
        term_sheet_id = f"ts_{str(action.action_id).replace('-', '')[:18]}"
        expires = tick + (
            self.settings.ventures.term_sheet_days * self.settings.clock.ticks_per_sim_day
        )
        valuation_mode = self.settings.mechanisms.get(
            "venture_valuation",
            "comparables_blend",
        )
        llm_weight_bp = (
            10_000
            if valuation_mode.lower() in {"off", "disabled", "investor_only"}
            else self.settings.ventures.valuation_llm_weight_bp
        )
        pre_money_cents = venture_pre_money_cents(
            self._venture_valuation_anchor(startup, tick),
            int(params["pre_money_cents"]),
            llm_weight_bp,
        )
        term = TermSheetState(
            term_sheet_id=term_sheet_id,
            startup_id=startup_id,
            investor_id=investor_id,
            pre_money_cents=pre_money_cents,
            amount_cents=int(params["amount_cents"]),
            security=str(params.get("security", "preferred")),
            liq_pref_bp=int(params.get("liq_pref_bp", self.settings.ventures.liq_pref_bp)),
            participating=bool(params.get("participating", False)),
            pro_rata=bool(params.get("pro_rata", True)),
            board_seat=bool(params.get("board_seat", False)),
            option_pool_bp=int(params.get("option_pool_bp", self.settings.ventures.option_pool_bp)),
            anti_dilution=str(params.get("anti_dilution", "broad_weighted")),
            issued_tick=tick,
            expires_tick=expires,
        )
        self.economy.ventures.term_sheets[term_sheet_id] = term
        return (
            emit(
                NewEvent(
                    TERM_SHEET_ISSUED,
                    {
                        "term_sheet_id": term_sheet_id,
                        "startup_id": startup_id,
                        "investor_id": investor_id,
                        "pre_money_cents": term.pre_money_cents,
                        "amount_cents": term.amount_cents,
                        "security": term.security,
                        "liq_pref_bp": term.liq_pref_bp,
                        "participating": term.participating,
                        "pro_rata": term.pro_rata,
                        "board_seat": term.board_seat,
                        "option_pool_bp": term.option_pool_bp,
                        "anti_dilution": term.anti_dilution,
                        "expires_tick": expires,
                    },
                    actor_id=investor_id,
                    subject_ids=(startup.firm_id,),
                )
            ),
        )

    def _invest(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.settings.ventures.enabled:
            return ()
        instrument = str(action.params.get("instrument", "round"))
        if instrument == "lp_commitment":
            return self._lp_commit(action, tick, emit)
        startup_id = str(action.params.get("target_id", ""))
        term_sheet_id = action.params.get("term_sheet_id")
        term = (
            self.economy.ventures.term_sheets.get(str(term_sheet_id))
            if term_sheet_id
            else next(
                (
                    row
                    for row in self.economy.ventures.term_sheets.values()
                    if row.startup_id == startup_id
                    and row.investor_id == action.actor_id
                    and row.status == "open"
                ),
                None,
            )
        )
        if (
            term is None
            or term.startup_id != startup_id
            or term.investor_id != action.actor_id
            or term.status != "open"
            or tick > term.expires_tick
        ):
            return ()
        amount = min(int(action.params.get("cents", 0)), term.amount_cents)
        if amount <= 0:
            return ()
        return self._close_round(term, amount, tick, emit)

    def _close_round(
        self,
        term: TermSheetState,
        amount_cents: int,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        startup = self.economy.ventures.startups[term.startup_id]
        firm = self.economy.firms[startup.firm_id]
        investor_deposit = self._deposit(term.investor_id)
        if self.economy.ledger.balance(investor_deposit) < amount_cents:
            return ()
        cap_rows = self._cap_rows(firm.firm_id)
        shares_pre = sum(row.shares for row in cap_rows)
        pool_shares = (
            shares_pre * term.option_pool_bp + (10_000 - term.option_pool_bp) - 1
        ) // max(1, 10_000 - term.option_pool_bp)
        shares_pre_pool = shares_pre + pool_shares
        price_per_share = max(1, term.pre_money_cents // max(1, shares_pre_pool))
        new_shares = amount_cents // price_per_share
        if new_shares <= 0:
            return ()
        round_id = f"rd_{term.term_sheet_id[3:]}"
        prior_rounds = [
            row
            for row in self.economy.ventures.rounds.values()
            if row.startup_id == startup.startup_id
        ]
        round_row = FundingRoundState(
            round_id=round_id,
            startup_id=startup.startup_id,
            stage=self._next_stage(startup.stage),
            pre_money_cents=term.pre_money_cents,
            amount_cents=amount_cents,
            post_money_cents=term.pre_money_cents + amount_cents,
            price_per_share_cents=price_per_share,
            new_shares=new_shares,
            lead_investor_id=term.investor_id,
            participants={term.investor_id: amount_cents},
            option_pool_shares=pool_shares,
            liq_pref_bp=term.liq_pref_bp,
            participating=term.participating,
            closed_tick=tick,
        )
        accepted = emit(
            NewEvent(
                TERM_SHEET_ACCEPTED,
                {
                    "term_sheet_id": term.term_sheet_id,
                    "round_id": round_id,
                },
                actor_id=startup.founder_id,
                subject_ids=(term.investor_id, firm.firm_id),
            )
        )
        expected_txn = self.economy.ledger.next_txn_id(tick)
        closed = emit(
            NewEvent(
                ROUND_CLOSED,
                {
                    "round_id": round_id,
                    "startup_id": startup.startup_id,
                    "stage": round_row.stage,
                    "pre_money_cents": term.pre_money_cents,
                    "amount_cents": amount_cents,
                    "post_money_cents": round_row.post_money_cents,
                    "price_per_share_cents": price_per_share,
                    "new_shares": new_shares,
                    "lead_investor_id": term.investor_id,
                    "participants": dict(round_row.participants),
                    "option_pool_shares": pool_shares,
                    "txn_id": str(expected_txn),
                },
                actor_id=term.investor_id,
                subject_ids=(firm.firm_id,),
            )
        )
        txn_id = self.economy.ledger.post_transaction(
            self.economy.ledger.transfer(
                investor_deposit,
                firm.ledger_account_id,
                amount_cents,
                "trade",
            ),
            tick=tick,
            cause=closed,
        )
        if txn_id != expected_txn:
            raise RuntimeError("venture round ledger ordinal diverged")
        events: list[Event] = [accepted, closed]
        if pool_shares:
            pool_key = self.economy.ventures.cap_key(
                firm.firm_id,
                "option_pool",
                "common",
            )
            pool = self.economy.ventures.cap_table.get(pool_key)
            before = pool.shares if pool is not None else 0
            if pool is None:
                pool = CapTableState(
                    firm_id=firm.firm_id,
                    holder_id="option_pool",
                    share_class="common",
                    shares=pool_shares,
                )
                self.economy.ventures.cap_table[pool_key] = pool
            else:
                pool.shares += pool_shares
            events.append(
                emit(
                    NewEvent(
                        OPTION_POOL_SET,
                        {
                            "firm_id": firm.firm_id,
                            "pool_shares": pool.shares,
                            "pool_bp": term.option_pool_bp,
                            "pre_money_pool": True,
                            "granted_to": [],
                        },
                        subject_ids=(firm.firm_id,),
                    )
                )
            )
            events.append(self._cap_event(pool, before, "option_pool", emit))
        investor_key = self.economy.ventures.cap_key(
            firm.firm_id,
            term.investor_id,
            term.security,
        )
        cap = self.economy.ventures.cap_table.get(investor_key)
        before = cap.shares if cap is not None else 0
        if cap is None:
            cap = CapTableState(
                firm_id=firm.firm_id,
                holder_id=term.investor_id,
                share_class=term.security,
                shares=new_shares,
                invested_cents=amount_cents,
                round_id=round_id,
                liq_pref_bp=term.liq_pref_bp,
                participating=term.participating,
                pro_rata=term.pro_rata,
                conversion_price_cents=price_per_share,
            )
            self.economy.ventures.cap_table[investor_key] = cap
        else:
            cap.shares += new_shares
            cap.invested_cents += amount_cents
        events.append(self._cap_event(cap, before, "round", emit))
        if prior_rounds:
            previous_price = prior_rounds[-1].price_per_share_cents
            if price_per_share < previous_price:
                events.append(
                    emit(
                        NewEvent(
                            DOWN_ROUND,
                            {
                                "round_id": round_id,
                                "prior_price_per_share_cents": previous_price,
                                "new_price_per_share_cents": price_per_share,
                                "decline_bp": 10_000
                                * (previous_price - price_per_share)
                                // previous_price,
                                "anti_dilution_applied": term.anti_dilution,
                                "extra_shares_issued": 0,
                            },
                            subject_ids=(firm.firm_id,),
                        )
                    )
                )
        self.economy.ventures.rounds[round_id] = round_row
        term.status = "accepted"
        startup.stage = round_row.stage
        startup.total_raised_cents += amount_cents
        for fund in self.economy.ventures.funds.values():
            if fund.firm_id == term.investor_id or fund.gp_agent_id == term.investor_id:
                fund.deployed_cents += amount_cents
        return tuple(events)

    def _lp_commit(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        fund_id = str(action.params.get("target_id", ""))
        fund = self.economy.ventures.funds.get(fund_id)
        cents = int(action.params.get("cents", 0))
        if fund is None or cents <= 0:
            return ()
        source = self._deposit(action.actor_id)
        destination = self._deposit(fund.firm_id)
        if self.economy.ledger.balance(source) < cents:
            return ()
        expected = self.economy.ledger.next_txn_id(tick)
        event = emit(
            NewEvent(
                CAPITAL_CALLED,
                {
                    "fund_id": fund_id,
                    "lp_id": action.actor_id,
                    "called_cents": cents,
                    "cumulative_called_cents": fund.called_cents + cents,
                    "txn_id": str(expected),
                },
                actor_id=fund.gp_agent_id,
                subject_ids=(action.actor_id,),
            )
        )
        txn_id = self.economy.ledger.post_transaction(
            self.economy.ledger.transfer(source, destination, cents, "trade"),
            tick=tick,
            cause=event,
        )
        if txn_id != expected:
            raise RuntimeError("capital call ledger ordinal diverged")
        fund.committed_cents += cents
        fund.called_cents += cents
        fund.lps[action.actor_id] = fund.lps.get(action.actor_id, 0) + cents
        return (event,)

    def _propose_acquisition(
        self,
        action: Action,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        acquirer_id = str(action.params.get("acquirer_id", ""))
        target_id = str(action.params.get("target_id", ""))
        acquirer = self.economy.firms.get(acquirer_id)
        target = self.economy.firms.get(target_id)
        if (
            acquirer is None
            or target is None
            or acquirer.founder_id != action.actor_id
            or target.status != "active"
        ):
            return ()
        anchor = self._acquisition_anchor_cents(target_id)
        anchor_mode = self.settings.mechanisms.get("ma_valuation_anchor", "on")
        default_premium_bp = (
            0
            if anchor_mode.lower() in {"off", "false", "disabled", "none"}
            else self.settings.ventures.acquisition_premium_bp
        )
        offer = int(action.params.get("offer_cents", 0)) or acquisition_offer_cents(
            anchor,
            default_premium_bp,
        )
        stock_ratio_bp = int(action.params.get("stock_ratio_bp", 0))
        integration_mode = str(action.params.get("integration_mode", "absorb"))
        if integration_mode == "asset_sale" and stock_ratio_bp:
            return ()
        cash_bp = 10_000 - stock_ratio_bp
        cash_required = offer * cash_bp // 10_000
        if cash_required > self.economy.ledger.balance(acquirer.ledger_account_id):
            return ()
        shares = max(
            1,
            sum(row.shares for row in self._acquisition_equity_rows(target_id)),
        )
        per_share = offer // shares
        premium = 10_000 * (offer - anchor) // anchor
        deal_id = f"ma_{str(action.action_id).replace('-', '')[:18]}"
        deal = AcquisitionState(
            deal_id=deal_id,
            acquirer_id=acquirer_id,
            target_id=target_id,
            offer_cents=offer,
            per_share_cents=per_share,
            consideration=str(action.params.get("consideration", "cash")),
            stock_ratio_bp=stock_ratio_bp,
            premium_bp=premium,
            integration_mode=integration_mode,
            financing=str(action.params.get("financing", "cash")),
            proposed_tick=tick,
            expires_tick=tick + 30 * self.settings.clock.ticks_per_sim_day,
        )
        self.economy.ventures.acquisitions[deal_id] = deal
        return (
            emit(
                NewEvent(
                    ACQUISITION_PROPOSED,
                    {
                        "deal_id": deal_id,
                        "acquirer_id": acquirer_id,
                        "target_id": target_id,
                        "offer_cents": offer,
                        "per_share_cents": per_share,
                        "consideration": deal.consideration,
                        "stock_ratio_bp": deal.stock_ratio_bp,
                        "premium_bp": premium,
                        "integration_mode": deal.integration_mode,
                        "expires_tick": deal.expires_tick,
                        "financing": deal.financing,
                    },
                    actor_id=action.actor_id,
                    subject_ids=(acquirer_id, target_id),
                )
            ),
        )

    def _acquisition_anchor_cents(self, target_id: str) -> int:
        firm = self.economy.firms[target_id]
        book_value = max(1, self._firm_balance_sheet_net_worth(target_id))
        symbol = self._listed_symbol(target_id)
        if symbol is not None:
            security = self.economy.exchange.securities[symbol]
            return max(book_value, security.last_price_cents * security.shares_outstanding)
        multiple = self.settings.ventures.sector_multiple_bp.get(
            firm.sector,
            self.settings.ventures.sector_multiple_bp.get("default", 10_000),
        )
        revenue_comp = firm.cumulative_revenue_cents * multiple // 10_000
        round_comps = sorted(
            row.pre_money_cents
            for row in self.economy.ventures.rounds.values()
            if (startup := self.economy.ventures.startups.get(row.startup_id)) is not None
            and startup.firm_id != target_id
            and startup.sector == firm.sector
        )
        comparable = 0
        if round_comps:
            middle = len(round_comps) // 2
            comparable = (
                round_comps[middle]
                if len(round_comps) % 2
                else (round_comps[middle - 1] + round_comps[middle]) // 2
            )
        candidates = sorted((book_value, revenue_comp, comparable))
        return max(book_value, candidates[1])

    def _tender(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        deal_id = action.params.get("deal_id")
        if not deal_id:
            return ()
        deal = self.economy.ventures.acquisitions.get(str(deal_id))
        if deal is None or deal.status != "proposed" or tick > deal.expires_tick:
            return ()
        equity_rows = self._acquisition_equity_rows(deal.target_id)
        holder_shares = sum(row.shares for row in equity_rows if row.holder_id == action.actor_id)
        qty = min(holder_shares, int(action.params.get("qty", 0)))
        if qty <= 0:
            return ()
        deal.accepting_holders[action.actor_id] = max(
            qty,
            deal.accepting_holders.get(action.actor_id, 0),
        )
        total_shares = sum(row.shares for row in equity_rows)
        accepting = min(total_shares, sum(deal.accepting_holders.values()))
        accepting_bp = 10_000 * accepting // max(1, total_shares)
        if accepting_bp < self.settings.ventures.acquisition_threshold_bp:
            return ()
        is_public = self._listed_symbol(deal.target_id) is not None
        deal.accepting_bp = accepting_bp
        deal.drag_along_applied = (
            not is_public and accepting_bp >= self.settings.ventures.drag_along_bp
        )
        deal.squeeze_out_applied = (
            is_public and accepting_bp >= self.settings.ventures.squeeze_out_bp
        )
        approved = emit(
            NewEvent(
                ACQUISITION_APPROVED,
                {
                    "deal_id": deal.deal_id,
                    "accepting_holders": dict(sorted(deal.accepting_holders.items())),
                    "accepting_bp": accepting_bp,
                    "threshold_bp": self.settings.ventures.acquisition_threshold_bp,
                    "drag_along_applied": deal.drag_along_applied,
                    "squeeze_out_applied": deal.squeeze_out_applied,
                },
                subject_ids=(deal.acquirer_id, deal.target_id),
            )
        )
        deal.status = "approved"
        return (approved, *self._complete_acquisition(deal, tick, emit))

    def _complete_acquisition(
        self,
        deal: AcquisitionState,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        equity_rows = self._acquisition_equity_rows(deal.target_id)
        acquired_rows = self._acquired_equity_rows(deal, equity_rows)
        eligible_shares = sum(row.shares for row in equity_rows)
        acquired_shares = sum(row.shares for row in acquired_rows)
        if acquired_shares <= 0 or eligible_shares <= 0:
            return ()
        consideration_cents = (
            deal.offer_cents
            if deal.integration_mode == "asset_sale"
            else deal.offer_cents * acquired_shares // eligible_shares
        )
        rounds = [
            row
            for row in self.economy.ventures.rounds.values()
            if self.economy.ventures.startups.get(row.startup_id) is not None
            and self.economy.ventures.startups[row.startup_id].firm_id == deal.target_id
        ]
        cash_cents = consideration_cents * (10_000 - deal.stock_ratio_bp) // 10_000
        distribution = (
            venture_waterfall(cash_cents, acquired_rows, rounds)
            if cash_cents and deal.integration_mode != "asset_sale"
            else {}
        )
        events: list[Event] = []
        if distribution:
            events.append(
                emit(
                    NewEvent(
                        WATERFALL_APPLIED,
                        {
                            "firm_id": deal.target_id,
                            "proceeds_cents": cash_cents,
                            "tranches": dict(distribution),
                        },
                        subject_ids=(deal.target_id,),
                    )
                )
            )
        expected = self.economy.ledger.next_txn_id(tick) if cash_cents else None
        asset_sale_event: Event | None = None
        if deal.integration_mode == "asset_sale":
            asset_sale_event = emit(
                NewEvent(
                    ASSET_SALE,
                    {
                        "deal_id": deal.deal_id,
                        "seller_id": deal.target_id,
                        "buyer_id": deal.acquirer_id,
                        "assets": self._asset_manifest(deal.target_id),
                        "cents": cash_cents,
                        "txn_id": str(expected) if expected is not None else None,
                    },
                    actor_id=deal.acquirer_id,
                    subject_ids=(deal.target_id,),
                )
            )
            events.append(asset_sale_event)
        completed = emit(
            NewEvent(
                ACQUISITION_COMPLETED,
                {
                    "deal_id": deal.deal_id,
                    "price_cents": consideration_cents,
                    "per_share_cents": deal.per_share_cents,
                    "integration_mode": deal.integration_mode,
                    "txn_id": str(expected) if expected is not None else None,
                    "waterfall_ref": (
                        "asset_sale" if deal.integration_mode == "asset_sale" else deal.target_id
                    ),
                },
                subject_ids=(deal.acquirer_id, deal.target_id),
            )
        )
        if cash_cents:
            source = self._deposit(deal.acquirer_id)
            if deal.integration_mode == "asset_sale":
                legs = list(
                    self.economy.ledger.transfer(
                        source,
                        self._deposit(deal.target_id),
                        cash_cents,
                        "trade",
                    )
                )
            else:
                legs = []
                for holder_id, cents in sorted(distribution.items()):
                    if cents:
                        legs.extend(
                            self.economy.ledger.transfer(
                                source,
                                self._deposit(holder_id),
                                cents,
                                "trade",
                            )
                        )
            txn_id = self.economy.ledger.post_transaction(
                _coalesce(legs),
                tick=tick,
                cause=asset_sale_event or completed,
            )
            if txn_id != expected:
                raise RuntimeError("acquisition ledger ordinal diverged")
        events.append(completed)
        if deal.stock_ratio_bp:
            recipients = (
                (CapTableState(deal.target_id, deal.target_id, "common", 1),)
                if deal.integration_mode == "asset_sale"
                else tuple(acquired_rows)
            )
            events.extend(
                self._issue_acquirer_shares(
                    deal,
                    recipients,
                    consideration_cents,
                    emit,
                )
            )
        if deal.integration_mode != "asset_sale":
            events.extend(self._transfer_target_equity(deal, acquired_rows, emit))
        events.extend(self._integrate(deal, tick, emit, completed))
        if deal.integration_mode != "asset_sale":
            for startup in self.economy.ventures.startups.values():
                if startup.firm_id != deal.target_id:
                    continue
                startup.status = "exited"
                events.append(
                    emit(
                        NewEvent(
                            EXIT_COMPLETED,
                            {
                                "startup_id": startup.startup_id,
                                "type": "acquisition",
                                "gross_proceeds_cents": consideration_cents,
                                "distribution": dict(distribution),
                                "multiple_bp": 10_000
                                * consideration_cents
                                // max(1, startup.total_raised_cents),
                                "holding_period_ticks": tick - startup.founded_tick,
                            },
                            subject_ids=(startup.firm_id,),
                        )
                    )
                )
        deal.status = "completed"
        return tuple(events)

    def _listed_symbol(self, firm_id: str) -> str | None:
        return next(
            (
                row.symbol
                for row in sorted(
                    self.economy.exchange.securities.values(),
                    key=lambda item: item.symbol,
                )
                if row.issuer_firm_id == firm_id and row.status == "listed"
            ),
            None,
        )

    def _acquisition_equity_rows(self, firm_id: str) -> list[CapTableState]:
        symbol = self._listed_symbol(firm_id)
        if symbol is None:
            return [
                row
                for row in self._cap_rows(firm_id)
                if row.holder_id != "option_pool" and row.shares > 0
            ]
        return [
            CapTableState(firm_id, holding.holder_id, "common", holding.qty)
            for holding in sorted(
                self.economy.exchange.holdings.values(),
                key=lambda row: row.holder_id,
            )
            if holding.symbol == symbol and holding.qty > 0
        ]

    def _acquired_equity_rows(
        self,
        deal: AcquisitionState,
        equity_rows: Sequence[CapTableState],
    ) -> list[CapTableState]:
        force_all = deal.drag_along_applied or deal.squeeze_out_applied
        remaining = dict(deal.accepting_holders)
        result: list[CapTableState] = []
        for row in equity_rows:
            shares = row.shares if force_all else min(row.shares, remaining.get(row.holder_id, 0))
            if shares <= 0:
                continue
            remaining[row.holder_id] = max(0, remaining.get(row.holder_id, 0) - shares)
            invested = row.invested_cents * shares // max(1, row.shares)
            result.append(replace(row, shares=shares, invested_cents=invested))
        return result

    def _issue_acquirer_shares(
        self,
        deal: AcquisitionState,
        target_rows: Sequence[CapTableState],
        consideration_cents: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        symbol = self._listed_symbol(deal.acquirer_id)
        security = self.economy.exchange.securities.get(symbol) if symbol else None
        acquirer_shares = (
            security.shares_outstanding
            if security is not None
            else max(1, self.economy.ventures.shares(deal.acquirer_id))
        )
        share_price = (
            security.last_price_cents
            if security is not None
            else max(
                1,
                self.economy.ledger.net_worth(deal.acquirer_id) // max(1, acquirer_shares),
            )
        )
        stock_value = consideration_cents * deal.stock_ratio_bp // 10_000
        issued = stock_value // share_price
        weights: dict[str, int] = defaultdict(int)
        for row in target_rows:
            if row.shares > 0:
                weights[row.holder_id] += row.shares
        allocation = allocate(
            issued,
            sorted(weights.items()),
        )
        if security is not None and symbol is not None:
            security.shares_outstanding += issued
            for holder_id, shares in sorted(allocation.items()):
                holding = self.economy.exchange.holding(holder_id, symbol)
                old_qty = holding.qty
                holding.qty += shares
                holding.avg_cost_cents = (
                    old_qty * holding.avg_cost_cents + shares * share_price
                ) // max(1, holding.qty)
            return ()
        events: list[Event] = []
        for holder_id, shares in allocation.items():
            key = self.economy.ventures.cap_key(
                deal.acquirer_id,
                holder_id,
                "common",
            )
            cap_row = self.economy.ventures.cap_table.get(key)
            before = cap_row.shares if cap_row is not None else 0
            if cap_row is None:
                cap_row = CapTableState(
                    firm_id=deal.acquirer_id,
                    holder_id=holder_id,
                    share_class="common",
                    shares=shares,
                )
                self.economy.ventures.cap_table[key] = cap_row
            else:
                cap_row.shares += shares
            events.append(self._cap_event(cap_row, before, "acquisition", emit))
        return tuple(events)

    def _transfer_target_equity(
        self,
        deal: AcquisitionState,
        acquired_rows: Sequence[CapTableState],
        emit: Emit,
    ) -> tuple[Event, ...]:
        symbol = self._listed_symbol(deal.target_id)
        if symbol is not None:
            transferred = 0
            for acquired in acquired_rows:
                holding = self.economy.exchange.holding(acquired.holder_id, symbol)
                holding.qty -= acquired.shares
                holding.locked_qty = min(holding.locked_qty, holding.qty)
                transferred += acquired.shares
            acquirer_holding = self.economy.exchange.holding(deal.acquirer_id, symbol)
            acquirer_holding.qty += transferred
            return ()
        events: list[Event] = []
        transferred = 0
        for acquired in acquired_rows:
            key = self.economy.ventures.cap_key(
                deal.target_id,
                acquired.holder_id,
                acquired.share_class,
            )
            row = self.economy.ventures.cap_table[key]
            before = row.shares
            row.shares -= acquired.shares
            transferred += acquired.shares
            events.append(self._cap_event(row, before, "acquisition", emit))
        if deal.drag_along_applied:
            option_key = self.economy.ventures.cap_key(
                deal.target_id,
                "option_pool",
                "common",
            )
            option_row = self.economy.ventures.cap_table.get(option_key)
            if option_row is not None and option_row.shares:
                before = option_row.shares
                option_row.shares = 0
                events.append(self._cap_event(option_row, before, "acquisition", emit))
        target_key = self.economy.ventures.cap_key(
            deal.target_id,
            deal.acquirer_id,
            "common",
        )
        target_row = self.economy.ventures.cap_table.get(target_key)
        before = target_row.shares if target_row is not None else 0
        if target_row is None:
            target_row = CapTableState(
                firm_id=deal.target_id,
                holder_id=deal.acquirer_id,
                share_class="common",
                shares=transferred,
            )
            self.economy.ventures.cap_table[target_key] = target_row
        else:
            target_row.shares += transferred
        events.append(self._cap_event(target_row, before, "acquisition", emit))
        return tuple(events)

    def _asset_manifest(self, firm_id: str) -> dict[str, Any]:
        inventory = {
            row.sku: row.quantity
            for row in sorted(
                self.economy.inventory.values(),
                key=lambda item: item.sku,
            )
            if row.firm_id == firm_id and row.quantity > 0
        }
        firm = self.economy.firms[firm_id]
        return {
            "inventory": inventory,
            "capital": firm.capital_cents,
            "skus": sorted(inventory),
            "places": [firm.place_id],
        }

    def _transfer_inventory(self, target_id: str, acquirer_id: str) -> list[str]:
        transferred: list[str] = []
        for row in sorted(
            self.economy.inventory.values(),
            key=lambda item: (item.sku, item.firm_id),
        ):
            if row.firm_id != target_id or row.quantity <= 0:
                continue
            key = f"{acquirer_id}:{row.sku}"
            destination = self.economy.inventory.get(key)
            if destination is None:
                destination = InventoryState(
                    firm_id=acquirer_id,
                    sku=row.sku,
                    unit_cost_cents=row.unit_cost_cents,
                    price_cents=row.price_cents,
                    markup_bp=row.markup_bp,
                )
                self.economy.inventory[key] = destination
            total_cost = (
                destination.quantity * destination.unit_cost_cents
                + row.quantity * row.unit_cost_cents
            )
            destination.quantity += row.quantity
            destination.unit_cost_cents = total_cost // max(1, destination.quantity)
            destination.carry_micro += row.carry_micro
            row.quantity = 0
            row.carry_micro = 0
            transferred.append(row.sku)
        return transferred

    def _transfer_productive_assets(
        self,
        target: FirmState,
        acquirer: FirmState,
        *,
        apply_synergy: bool,
    ) -> int:
        acquirer_capital = acquirer.capital_cents
        target_capital = target.capital_cents
        total_capital = acquirer_capital + target_capital
        if total_capital <= 0:
            return 0
        synergy_mode = self.settings.mechanisms.get(
            "ventures_integration_synergy",
            "on",
        )
        configured_delta = (
            self.settings.ventures.integration_synergy_bp
            if apply_synergy and synergy_mode.lower() not in {"off", "false", "disabled", "none"}
            else 0
        )
        productivity, realised_delta = integrated_productivity_bp(
            acquirer.productivity_bp,
            acquirer_capital,
            target.productivity_bp,
            target_capital,
            configured_delta,
        )
        acquirer.productivity_bp = productivity
        acquirer.capital_cents = total_capital
        target.capital_cents = 0
        return realised_delta

    def _ensure_lender_deposit(
        self,
        borrower_id: str,
        lender_id: str,
        tick: int,
    ) -> str | None:
        if lender_id not in self.economy.banks:
            return None
        deposits = [
            account_id
            for account_id in self.economy.ledger.accounts_of(borrower_id)
            if parse_account_id(account_id)[0] == "dep"
            and parse_account_id(account_id)[2] == lender_id
        ]
        if deposits:
            return sorted(deposits)[0]
        return self.economy.ledger.open_account(
            "dep",
            borrower_id,
            "firm",
            bank_id=lender_id,
            tick=tick,
        )

    def _transfer_loans(
        self,
        target_id: str,
        acquirer_id: str,
        tick: int,
        cause: Event,
    ) -> list[str]:
        transferred: list[str] = []
        for loan in sorted(self.economy.loans.values(), key=lambda row: row.loan_id):
            if loan.borrower_id != target_id or loan.status not in {"current", "delinquent"}:
                continue
            old_payable = loan.borrower_payable_account_id
            liability = max(0, -self.economy.ledger.balance(old_payable))
            new_payable = self.economy.ledger.open_account(
                "lnp",
                acquirer_id,
                "firm",
                ref=loan.loan_id,
                tick=tick,
            )
            if liability:
                self.economy.ledger.post_transaction(
                    (
                        Leg(old_payable, 1, liability, "transfer"),
                        Leg(new_payable, -1, liability, "transfer"),
                    ),
                    tick=tick,
                    cause=cause,
                )
            self.economy.ledger.close_account(old_payable, tick=tick)
            self._ensure_lender_deposit(acquirer_id, loan.lender_id, tick)
            loan.borrower_id = acquirer_id
            loan.borrower_payable_account_id = new_payable
            transferred.append(loan.loan_id)
        return transferred

    def _integrate(
        self,
        deal: AcquisitionState,
        tick: int,
        emit: Emit,
        cause: Event,
    ) -> tuple[Event, ...]:
        target = self.economy.firms[deal.target_id]
        acquirer = self.economy.firms[deal.acquirer_id]
        transferred = 0
        redundancies = 0
        sku_transfers: list[str] = []
        productivity_delta_bp = 0
        loans_transferred: list[str] = []
        events: list[Event] = []
        if deal.integration_mode == "absorb":
            active_target = [
                row
                for row in self.economy.employments.values()
                if row.firm_id == target.firm_id and row.ended_tick is None
            ]
            acquirer_roles = {
                row.occupation
                for row in self.economy.employments.values()
                if row.firm_id == acquirer.firm_id and row.ended_tick is None
            }
            overlapping = [row for row in active_target if row.occupation in acquirer_roles]
            redundancy_n = bp(len(overlapping), self.settings.ventures.redundancy_bp)
            redundant_ids = {
                row.employment_id for row in redundancy_order(overlapping, tick=tick)[:redundancy_n]
            }
            for employment in sorted(active_target, key=lambda row: row.employment_id):
                if employment.employment_id not in redundant_ids:
                    employment.firm_id = acquirer.firm_id
                    transferred += 1
                    continue
                severance = bp(
                    employment.wage_cents,
                    self.settings.labour.severance_periods_bp,
                )
                fired = emit(
                    NewEvent(
                        FIRED,
                        {
                            "employment_id": employment.employment_id,
                            "agent_id": employment.agent_id,
                            "firm_id": target.firm_id,
                            "reason": "acquisition",
                            "severance_cents": severance,
                            "notice_ticks": self.settings.labour.notice_ticks,
                        },
                        actor_id=acquirer.firm_id,
                        subject_ids=(employment.agent_id,),
                    )
                )
                if severance:
                    self.economy.ledger.post_transaction(
                        self.economy.ledger.transfer(
                            self._deposit(acquirer.firm_id),
                            self._deposit(employment.agent_id),
                            severance,
                            "wage",
                        ),
                        tick=tick,
                        cause=fired,
                    )
                employment.ended_tick = tick + self.settings.labour.notice_ticks
                self.population[employment.agent_id].employment_status = "unemployed"
                redundancies += 1
                events.append(fired)
            target.headcount = 0
            acquirer.headcount += transferred
            sku_transfers = self._transfer_inventory(target.firm_id, acquirer.firm_id)
            productivity_delta_bp = self._transfer_productive_assets(
                target,
                acquirer,
                apply_synergy=True,
            )
            loans_transferred = self._transfer_loans(
                target.firm_id,
                acquirer.firm_id,
                tick,
                cause,
            )
            target.status = "acquired"
            target.dissolved_tick = tick
        elif deal.integration_mode == "standalone":
            target.status = "subsidiary"
            transferred = sum(
                row.firm_id == target.firm_id and row.ended_tick is None
                for row in self.economy.employments.values()
            )
        elif deal.integration_mode == "asset_sale":
            transferred = sum(
                row.firm_id == target.firm_id and row.ended_tick is None
                for row in self.economy.employments.values()
            )
            sku_transfers = self._transfer_inventory(target.firm_id, acquirer.firm_id)
            productivity_delta_bp = self._transfer_productive_assets(
                target,
                acquirer,
                apply_synergy=False,
            )
        symbol = self._listed_symbol(target.firm_id)
        if symbol is not None and (deal.integration_mode == "absorb" or deal.squeeze_out_applied):
            events.extend(self.exchange.delist(symbol, "acquisition", tick, emit))
        events.append(
            emit(
                NewEvent(
                    INTEGRATION_COMPLETED,
                    {
                        "deal_id": deal.deal_id,
                        "headcount_retained": transferred,
                        "redundancies": redundancies,
                        "sku_transfers": sku_transfers,
                        "productivity_delta_bp": productivity_delta_bp,
                        "loans_transferred": loans_transferred,
                    },
                    subject_ids=(deal.acquirer_id, deal.target_id),
                )
            )
        )
        return tuple(events)

    def _file_action(self, action: Action, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.settings.bankruptcy.enabled:
            return ()
        entity_id = str(action.params.get("entity_id") or action.actor_id)
        if entity_id != action.actor_id and not (
            entity_id in self.economy.firms
            and self.economy.firms[entity_id].founder_id == action.actor_id
        ):
            return ()
        return self._file_case(entity_id, str(action.params.get("reason", "voluntary")), tick, emit)

    def _liquidation_end_tick(self, filed_tick: int) -> int:
        sessions = max(1, self.settings.bankruptcy.liquidation_days)
        ticks_per_day = self.settings.clock.ticks_per_sim_day
        if self.settings.clock.profile == "chronicle":
            return filed_tick + sessions * ticks_per_day
        filed_day = filed_tick // ticks_per_day
        return (filed_day + sessions) * ticks_per_day + 9

    def _file_case(
        self,
        entity_id: str,
        trigger: str,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        if any(
            row.entity_id == entity_id and row.status == "open"
            for row in self.economy.ventures.bankruptcies.values()
        ):
            return ()
        entity_type = (
            "firm"
            if entity_id in self.economy.firms
            else "fund"
            if entity_id in self.economy.ventures.funds
            else "agent"
        )
        listed_book_qty = {
            holding.symbol: holding.qty
            for holding in sorted(
                self.economy.exchange.holdings.values(),
                key=lambda row: (row.symbol, row.holder_id),
            )
            if holding.holder_id == entity_id
            and holding.qty > 0
            and (security := self.economy.exchange.securities.get(holding.symbol)) is not None
            and security.status == "listed"
        }
        listed_book_cents = {
            symbol: qty * self.economy.exchange.securities[symbol].last_price_cents
            for symbol, qty in sorted(listed_book_qty.items())
        }
        inventory_book = sum(
            row.quantity * row.unit_cost_cents
            for row in self.economy.inventory.values()
            if row.firm_id == entity_id and row.quantity > 0
        )
        capital_book = (
            max(0, self.economy.firms[entity_id].capital_cents)
            if entity_id in self.economy.firms
            else 0
        )
        unlisted_book = sum(
            max(0, row.invested_cents)
            for row in self.economy.ventures.cap_table.values()
            if row.holder_id == entity_id
            and row.firm_id != entity_id
            and not any(
                security.issuer_firm_id == row.firm_id and security.status == "listed"
                for security in self.economy.exchange.securities.values()
            )
        )
        assets = (
            max(0, self.economy.ledger.liquid(entity_id))
            + sum(listed_book_cents.values())
            + inventory_book
            + capital_book
            + unlisted_book
        )
        loans = [
            row
            for row in self.economy.loans.values()
            if row.borrower_id == entity_id and row.status not in {"repaid", "written_off"}
        ]
        liabilities = sum(row.outstanding_cents for row in loans)
        case_id = f"bc_{entity_id}_{tick:010d}"
        case = BankruptcyCaseState(
            case_id=case_id,
            entity_id=entity_id,
            entity_type=entity_type,
            trigger=trigger,
            assets_cents=assets,
            liabilities_cents=liabilities,
            filed_tick=tick,
            stay_until_tick=tick
            + self.settings.bankruptcy.stay_max_days * self.settings.clock.ticks_per_sim_day,
            liquidation_tick=self._liquidation_end_tick(tick),
            listed_book_qty=listed_book_qty,
            listed_book_cents=listed_book_cents,
        )
        self.economy.ventures.bankruptcies[case_id] = case
        if entity_id in self.economy.firms:
            self.economy.firms[entity_id].status = "bankrupt"
        filed = emit(
            NewEvent(
                BANKRUPTCY_FILED,
                {
                    "case_id": case_id,
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "trigger": trigger,
                    "assets_cents": assets,
                    "liabilities_cents": liabilities,
                    "filed_by": "self" if trigger == "voluntary" else "institution",
                    "petitioning_creditor_id": None,
                },
                actor_id=entity_id,
                subject_ids=(entity_id,),
            )
        )
        cancelled, order_ids, released, released_shares = self.exchange.cancel_entity(
            entity_id,
            tick,
            emit,
        )
        for position in self.economy.exchange.shorts.values():
            if position.trader_id == entity_id and position.status == "open" and position.qty > 0:
                forced_tick = tick + self.settings.clock.ticks_per_sim_day
                position.margin_deadline_tick = min(
                    position.margin_deadline_tick or forced_tick,
                    forced_tick,
                )
        allowed = {
            ActionType.FILE_BANKRUPTCY,
            ActionType.WORK,
            ActionType.MOVE_TO,
            ActionType.NULL_ACTION,
            ActionType.SLEEP,
            ActionType.EAT,
        }
        stay = emit(
            NewEvent(
                AUTOMATIC_STAY_IMPOSED,
                {
                    "case_id": case_id,
                    "entity_id": entity_id,
                    "cancelled_order_ids": list(order_ids),
                    "released_cents": released,
                    "released_shares": released_shares,
                    "blocked_action_types": sorted(
                        action_type.value
                        for action_type in ActionType
                        if action_type not in allowed
                    ),
                    "stay_until_tick": case.stay_until_tick,
                },
                subject_ids=(entity_id,),
            )
        )
        events: list[Event] = [filed, *cancelled, stay]
        claim_specs: list[tuple[str, int, int, str | None, str | None]] = []
        admin_fee = assets * self.settings.bankruptcy.admin_fee_bp // 10_000
        if admin_fee > 0:
            claim_specs.append(("gv_treasury", admin_fee, 2, None, None))
        if entity_id in self.economy.firms:
            wage_period_days = max(1, self.settings.credit.payment_interval_days)
            for employment in sorted(
                self.economy.employments.values(),
                key=lambda row: row.employment_id,
            ):
                if employment.firm_id != entity_id or employment.accrued_wage_cents <= 0:
                    continue
                wage_cap = (
                    employment.wage_cents
                    * self.settings.bankruptcy.wage_priority_days
                    // wage_period_days
                )
                claim_specs.append(
                    (
                        employment.agent_id,
                        min(employment.accrued_wage_cents, wage_cap),
                        2,
                        None,
                        None,
                    )
                )
        for assessment in sorted(
            self.economy.tax_assessments.values(),
            key=lambda row: row.assessment_id,
        ):
            outstanding_tax = assessment.assessed_cents - assessment.paid_cents
            if assessment.taxpayer_id == entity_id and outstanding_tax > 0:
                claim_specs.append(("gv_treasury", outstanding_tax, 3, None, None))
        for loan in sorted(loans, key=lambda row: row.loan_id):
            secured = (
                min(loan.outstanding_cents, loan.collateral_value_cents) if loan.collateral else 0
            )
            if secured:
                claim_specs.append(
                    (
                        loan.lender_id,
                        secured,
                        1,
                        canonical_json(loan.collateral),
                        loan.loan_id,
                    )
                )
            deficiency = loan.outstanding_cents - secured
            if deficiency:
                claim_specs.append(
                    (
                        loan.lender_id,
                        deficiency,
                        4,
                        None,
                        loan.loan_id,
                    )
                )
        for row in self._cap_rows(entity_id):
            if row.holder_id != "option_pool" and row.invested_cents > 0:
                claim_specs.append((row.holder_id, row.invested_cents, 5, None, None))

        for ordinal, (
            creditor_id,
            claim_cents,
            priority_class,
            collateral_ref,
            loan_id,
        ) in enumerate(claim_specs):
            claim_id = f"cl_{case_id}_{ordinal:04d}"
            claim = ClaimState(
                claim_id=claim_id,
                case_id=case_id,
                creditor_id=creditor_id,
                claim_cents=claim_cents,
                priority_class=priority_class,
                collateral_ref=collateral_ref,
                loan_id=loan_id,
            )
            self.economy.ventures.claims[claim_id] = claim
            events.append(
                emit(
                    NewEvent(
                        CLAIM_REGISTERED,
                        {
                            "case_id": case_id,
                            "creditor_id": claim.creditor_id,
                            "claim_cents": claim.claim_cents,
                            "priority_class": claim.priority_class,
                            "collateral_ref": claim.collateral_ref,
                            "loan_id": claim.loan_id,
                        },
                        subject_ids=(entity_id, claim.creditor_id),
                    )
                )
            )
        return tuple(events)

    def _solvent_firm_buyers(self, debtor_id: str, sector: str) -> list[FirmState]:
        buyers: list[FirmState] = []
        for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
            if (
                firm.firm_id == debtor_id
                or firm.sector != sector
                or firm.status not in {"active", "subsidiary"}
                or self.economy.ledger.net_worth(firm.firm_id) < 0
            ):
                continue
            try:
                deposit = self._deposit(firm.firm_id)
            except LedgerError:
                continue
            if self.economy.ledger.balance(deposit) > 0:
                buyers.append(firm)
        return buyers

    def _post_asset_sale(
        self,
        *,
        case: BankruptcyCaseState,
        item: str,
        asset_ref: str,
        book_cents: int,
        realised_cents: int,
        haircut_bp: int,
        buyer_id: str,
        tick: int,
        emit: Emit,
        extra: Mapping[str, object] | None = None,
    ) -> Event:
        expected = self.economy.ledger.next_txn_id(tick)
        payload: dict[str, object] = {
            "case_id": case.case_id,
            "item": item,
            "asset_ref": asset_ref,
            "book_cents": book_cents,
            "realised_cents": realised_cents,
            "haircut_bp": haircut_bp,
            "buyer_id": buyer_id,
            "txn_id": str(expected),
        }
        payload.update(extra or {})
        event = emit(
            NewEvent(
                ASSETS_LIQUIDATED,
                payload,
                subject_ids=(case.entity_id, buyer_id),
            )
        )
        txn_id = self.economy.ledger.post_transaction(
            self.economy.ledger.transfer(
                self._deposit(buyer_id),
                self._deposit(case.entity_id),
                realised_cents,
                "trade",
            ),
            tick=tick,
            cause=event,
        )
        if txn_id != expected:
            raise RuntimeError("asset-sale ledger ordinal diverged")
        return event

    def _realise_inventory(
        self,
        case: BankruptcyCaseState,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        if case.entity_id not in self.economy.firms:
            return ()
        debtor = self.economy.firms[case.entity_id]
        buyers = self._solvent_firm_buyers(case.entity_id, debtor.sector)
        events: list[Event] = []
        for inventory in sorted(
            self.economy.inventory.values(),
            key=lambda row: (row.firm_id, row.sku),
        ):
            if inventory.firm_id != case.entity_id or inventory.quantity <= 0:
                continue
            original_units = inventory.quantity
            unit_book = inventory.unit_cost_cents
            unit_price = bp(unit_book, self.settings.bankruptcy.inventory_haircut_bp)
            allocations = (
                allocate(
                    original_units,
                    [(row.firm_id, max(1, row.capital_cents)) for row in buyers],
                )
                if buyers and unit_price > 0
                else {}
            )
            sold_units = 0
            for buyer in buyers:
                proposed = allocations.get(buyer.firm_id, 0)
                affordable = self.economy.ledger.balance(self._deposit(buyer.firm_id)) // max(
                    1, unit_price
                )
                units = min(proposed, affordable)
                if units <= 0:
                    continue
                realised = units * unit_price
                events.append(
                    self._post_asset_sale(
                        case=case,
                        item="inventory",
                        asset_ref=inventory.sku,
                        book_cents=units * unit_book,
                        realised_cents=realised,
                        haircut_bp=10_000 - self.settings.bankruptcy.inventory_haircut_bp,
                        buyer_id=buyer.firm_id,
                        tick=tick,
                        emit=emit,
                        extra={"sku": inventory.sku, "units": units},
                    )
                )
                key = f"{buyer.firm_id}:{inventory.sku}"
                destination = self.economy.inventory.get(key)
                if destination is None:
                    destination = InventoryState(
                        firm_id=buyer.firm_id,
                        sku=inventory.sku,
                        quantity=0,
                        unit_cost_cents=max(1, unit_price),
                        price_cents=max(1, unit_price),
                    )
                    self.economy.inventory[key] = destination
                old_value = destination.quantity * destination.unit_cost_cents
                destination.quantity += units
                destination.unit_cost_cents = (old_value + realised) // destination.quantity
                destination.price_cents = max(
                    destination.price_cents,
                    destination.unit_cost_cents,
                )
                sold_units += units
            unsold = original_units - sold_units
            inventory.quantity = 0
            if unsold:
                events.append(
                    emit(
                        NewEvent(
                            INVENTORY_WRITTEN_OFF,
                            {
                                "firm_id": case.entity_id,
                                "sku": inventory.sku,
                                "units": unsold,
                                "unit_cost_cents": unit_book,
                                "value_cents": unsold * unit_book,
                                "reason": "bankruptcy_no_buyer",
                            },
                            actor_id=case.entity_id,
                        )
                    )
                )
        return tuple(events)

    def _realise_capital(
        self,
        case: BankruptcyCaseState,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        firm = self.economy.firms.get(case.entity_id)
        if firm is None or firm.capital_cents <= 0:
            return ()
        book = firm.capital_cents
        rate = self.settings.bankruptcy.capital_haircut_bp
        buyers = self._solvent_firm_buyers(case.entity_id, firm.sector)
        allocations = (
            allocate(book, [(row.firm_id, max(1, row.capital_cents)) for row in buyers])
            if buyers and rate > 0
            else {}
        )
        events: list[Event] = []
        sold_book = 0
        for buyer in buyers:
            proposed_book = allocations.get(buyer.firm_id, 0)
            cash = self.economy.ledger.balance(self._deposit(buyer.firm_id))
            affordable_book = cash * 10_000 // rate if rate else 0
            transferred_book = min(proposed_book, affordable_book)
            realised = bp(transferred_book, rate)
            if transferred_book <= 0 or realised <= 0:
                continue
            events.append(
                self._post_asset_sale(
                    case=case,
                    item="capital",
                    asset_ref=firm.firm_id,
                    book_cents=transferred_book,
                    realised_cents=realised,
                    haircut_bp=10_000 - rate,
                    buyer_id=buyer.firm_id,
                    tick=tick,
                    emit=emit,
                )
            )
            buyer.capital_cents += realised
            sold_book += transferred_book
        unsold_book = book - sold_book
        firm.capital_cents = 0
        if unsold_book:
            events.append(
                emit(
                    NewEvent(
                        ASSETS_LIQUIDATED,
                        {
                            "case_id": case.case_id,
                            "item": "capital",
                            "asset_ref": firm.firm_id,
                            "book_cents": unsold_book,
                            "realised_cents": 0,
                            "haircut_bp": 10_000,
                            "buyer_id": None,
                            "txn_id": None,
                            "disposition": "written_off_no_buyer",
                        },
                        subject_ids=(case.entity_id,),
                    )
                )
            )
        return tuple(events)

    def _latest_private_price(self, firm_id: str, row: CapTableState) -> int:
        startup = next(
            (
                startup
                for startup in self.economy.ventures.startups.values()
                if startup.firm_id == firm_id
            ),
            None,
        )
        rounds = (
            [
                round_row
                for round_row in self.economy.ventures.rounds.values()
                if round_row.startup_id == startup.startup_id
            ]
            if startup is not None
            else []
        )
        if rounds:
            return max(
                rounds, key=lambda value: (value.closed_tick, value.round_id)
            ).price_per_share_cents
        if row.conversion_price_cents > 0:
            return row.conversion_price_cents
        return max(1, row.invested_cents // max(1, row.shares))

    def _realise_unlisted_equity(
        self,
        case: BankruptcyCaseState,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        events: list[Event] = []
        debtor_rows = sorted(
            (
                row
                for row in self.economy.ventures.cap_table.values()
                if row.holder_id == case.entity_id
                and row.firm_id != case.entity_id
                and row.shares > 0
                and not any(
                    security.issuer_firm_id == row.firm_id and security.status == "listed"
                    for security in self.economy.exchange.securities.values()
                )
            ),
            key=lambda row: (row.firm_id, row.share_class),
        )
        for debtor_row in debtor_rows:
            original_shares = debtor_row.shares
            last_price = self._latest_private_price(debtor_row.firm_id, debtor_row)
            unit_price = bp(last_price, self.settings.bankruptcy.unlisted_haircut_bp)
            holder_weights: dict[str, int] = defaultdict(int)
            for row in self._cap_rows(debtor_row.firm_id):
                if row.holder_id not in {case.entity_id, "option_pool"} and row.shares > 0:
                    holder_weights[row.holder_id] += row.shares
            eligible: list[str] = []
            for holder_id in sorted(holder_weights):
                try:
                    deposit = self._deposit(holder_id)
                except LedgerError:
                    continue
                if self.economy.ledger.balance(deposit) > 0:
                    eligible.append(holder_id)
            allocations = (
                allocate(
                    original_shares,
                    [(holder_id, holder_weights[holder_id]) for holder_id in eligible],
                )
                if eligible and unit_price > 0
                else {}
            )
            transferred = 0
            before_seller = debtor_row.shares
            for holder_id in eligible:
                proposed = allocations.get(holder_id, 0)
                affordable = self.economy.ledger.balance(self._deposit(holder_id)) // max(
                    1, unit_price
                )
                shares = min(proposed, affordable)
                if shares <= 0:
                    continue
                realised = shares * unit_price
                events.append(
                    self._post_asset_sale(
                        case=case,
                        item="securities",
                        asset_ref=f"{debtor_row.firm_id}:{debtor_row.share_class}",
                        book_cents=shares * last_price,
                        realised_cents=realised,
                        haircut_bp=10_000 - self.settings.bankruptcy.unlisted_haircut_bp,
                        buyer_id=holder_id,
                        tick=tick,
                        emit=emit,
                        extra={"shares": shares, "listed": False},
                    )
                )
                buyer_key = self.economy.ventures.cap_key(
                    debtor_row.firm_id,
                    holder_id,
                    debtor_row.share_class,
                )
                buyer_row = self.economy.ventures.cap_table.get(buyer_key)
                if buyer_row is None:
                    buyer_row = CapTableState(
                        firm_id=debtor_row.firm_id,
                        holder_id=holder_id,
                        share_class=debtor_row.share_class,
                        shares=0,
                        round_id=debtor_row.round_id,
                        liq_pref_bp=debtor_row.liq_pref_bp,
                        participating=debtor_row.participating,
                        pro_rata=debtor_row.pro_rata,
                        conversion_price_cents=debtor_row.conversion_price_cents,
                    )
                    self.economy.ventures.cap_table[buyer_key] = buyer_row
                before_buyer = buyer_row.shares
                buyer_row.shares += shares
                buyer_row.invested_cents += realised
                events.append(
                    self._cap_event(
                        buyer_row,
                        before=before_buyer,
                        cause="bankruptcy_liquidation",
                        emit=emit,
                    )
                )
                transferred += shares
            debtor_row.shares = 0
            debtor_row.invested_cents = 0
            events.append(
                self._cap_event(
                    debtor_row,
                    before=before_seller,
                    cause="bankruptcy_liquidation",
                    emit=emit,
                )
            )
            unsold = original_shares - transferred
            if unsold:
                events.append(
                    emit(
                        NewEvent(
                            ASSETS_LIQUIDATED,
                            {
                                "case_id": case.case_id,
                                "item": "securities",
                                "asset_ref": (f"{debtor_row.firm_id}:{debtor_row.share_class}"),
                                "book_cents": unsold * last_price,
                                "realised_cents": 0,
                                "haircut_bp": 10_000,
                                "buyer_id": None,
                                "txn_id": None,
                                "shares": unsold,
                                "listed": False,
                                "disposition": "written_off_no_buyer",
                            },
                            subject_ids=(case.entity_id, debtor_row.firm_id),
                        )
                    )
                )
        return tuple(events)

    def _listed_liquidation_events(
        self,
        case: BankruptcyCaseState,
        emit: Emit,
    ) -> tuple[Event, ...]:
        events: list[Event] = []
        case_flag = f"bankruptcy:{case.case_id}"
        order_ids = {
            order.order_id
            for order in self.economy.exchange.orders.values()
            if order.trader_id == case.entity_id
            and order.side == "sell"
            and case_flag in order.flags
        }
        for symbol, initial_qty in sorted(case.listed_book_qty.items()):
            trades = [
                trade
                for trade in self.economy.exchange.trades
                if trade.symbol == symbol and trade.sell_order_id in order_ids
            ]
            sold_qty = sum(trade.qty for trade in trades)
            realised = sum(
                trade.price_cents * trade.qty - trade.commission_sell_cents for trade in trades
            )
            book = case.listed_book_cents.get(symbol, 0)
            events.append(
                emit(
                    NewEvent(
                        ASSETS_LIQUIDATED,
                        {
                            "case_id": case.case_id,
                            "item": "securities",
                            "asset_ref": symbol,
                            "book_cents": book,
                            "realised_cents": realised,
                            "haircut_bp": min(
                                10_000,
                                max(0, 10_000 - 10_000 * realised // max(1, book)),
                            ),
                            "buyer_id": "market" if sold_qty else None,
                            "txn_id": None,
                            "shares": sold_qty,
                            "initial_shares": initial_qty,
                            "listed": True,
                            "trade_ids": [trade.trade_id for trade in trades],
                        },
                        subject_ids=(case.entity_id, symbol),
                    )
                )
            )
        return tuple(events)

    def _advance_bankruptcies(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for case in sorted(
            self.economy.ventures.bankruptcies.values(),
            key=lambda row: row.case_id,
        ):
            if (
                case.status != "open"
                or case.liquidation_tick is None
                or tick < case.liquidation_tick
            ):
                continue
            events.extend(self._liquidate_case(case, tick, emit))
        return tuple(events)

    def _liquidate_case(
        self,
        case: BankruptcyCaseState,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        events: list[Event] = []
        events.extend(self._listed_liquidation_events(case, emit))
        events.extend(self._realise_unlisted_equity(case, tick, emit))
        events.extend(self._realise_inventory(case, tick, emit))
        events.extend(self._realise_capital(case, tick, emit))
        estate = max(0, self.economy.ledger.liquid(case.entity_id))
        exempt = 0
        if case.entity_type == "agent":
            exempt = min(
                estate,
                self.settings.economy.median_wage_cents
                * self.settings.bankruptcy.exempt_months
                // 12,
            )
        distributable = estate - exempt
        case.estate_cents = distributable
        events.append(
            emit(
                NewEvent(
                    ASSETS_LIQUIDATED,
                    {
                        "case_id": case.case_id,
                        "item": "deposits",
                        "book_cents": estate,
                        "realised_cents": estate,
                        "haircut_bp": 0,
                        "buyer_id": None,
                        "txn_id": None,
                    },
                    subject_ids=(case.entity_id,),
                )
            )
        )
        if exempt:
            events.append(
                emit(
                    NewEvent(
                        EXEMPTION_APPLIED,
                        {
                            "case_id": case.case_id,
                            "entity_id": case.entity_id,
                            "exempt_cents": exempt,
                            "basis": "one_sim_month_median_wage",
                        },
                        subject_ids=(case.entity_id,),
                    )
                )
            )
        claims = [
            row
            for row in self.economy.ventures.claims.values()
            if row.case_id == case.case_id and row.claim_cents > 0
        ]
        source = self._deposit(case.entity_id) if distributable else None
        total_claimed = sum(row.claim_cents for row in claims)
        waterfall_payments = priority_waterfall(distributable, claims)
        total_paid = 0
        for priority in range(1, 6):
            class_claims = [row for row in claims if row.priority_class == priority]
            if not class_claims:
                continue
            class_total = sum(row.claim_cents for row in class_claims)
            pool = sum(waterfall_payments.get(row.claim_id, 0) for row in class_claims)
            for claim in sorted(class_claims, key=lambda row: row.claim_id):
                paid = waterfall_payments.get(claim.claim_id, 0)
                claim.paid_cents = paid
                expected = self.economy.ledger.next_txn_id(tick) if paid else None
                event = emit(
                    NewEvent(
                        DISTRIBUTION_MADE,
                        {
                            "case_id": case.case_id,
                            "priority_class": priority,
                            "creditor_id": claim.creditor_id,
                            "claim_cents": claim.claim_cents,
                            "paid_cents": paid,
                            "class_recovery_bp": 10_000 * pool // max(1, class_total),
                            "txn_id": str(expected) if expected is not None else None,
                        },
                        subject_ids=(case.entity_id, claim.creditor_id),
                    )
                )
                if paid and source is not None:
                    reason = "loan" if claim.loan_id is not None else "transfer"
                    legs = self.economy.ledger.transfer(
                        source,
                        self._deposit(claim.creditor_id),
                        paid,
                        reason,
                    )
                    if claim.loan_id is not None:
                        loan = self.economy.loans[claim.loan_id]
                        principal = min(paid, loan.outstanding_cents)
                        legs.extend(
                            (
                                Leg(
                                    loan.lender_receivable_account_id,
                                    -1,
                                    principal,
                                    "loan",
                                ),
                                Leg(
                                    loan.borrower_payable_account_id,
                                    1,
                                    principal,
                                    "loan",
                                ),
                            )
                        )
                    txn_id = self.economy.ledger.post_transaction(
                        _coalesce(legs),
                        tick=tick,
                        cause=event,
                    )
                    if txn_id != expected:
                        raise RuntimeError("bankruptcy distribution ordinal diverged")
                    if claim.loan_id is not None:
                        loan.outstanding_cents -= principal
                events.append(event)
                total_paid += paid
        loan_ids = sorted({str(claim.loan_id) for claim in claims if claim.loan_id is not None})
        for loan_id in loan_ids:
            loan = self.economy.loans[loan_id]
            residual = loan.outstanding_cents
            if residual > 0:
                recovered = sum(claim.paid_cents for claim in claims if claim.loan_id == loan_id)
                events.extend(
                    write_off_loan(
                        loan_id,
                        residual,
                        recovered,
                        tick,
                        ctx=self.credit_context,
                        emit=emit,
                    )
                )
        written_off = max(0, total_claimed - total_paid)
        case.status = "discharged"
        case.resolved_tick = tick
        events.append(
            emit(
                NewEvent(
                    BANKRUPTCY_DISCHARGED,
                    {
                        "case_id": case.case_id,
                        "outcome": "liquidated",
                        "written_off_cents": written_off,
                        "blended_recovery_bp": 10_000 * total_paid // max(1, total_claimed),
                        "resolved_tick": tick,
                    },
                    subject_ids=(case.entity_id,),
                )
            )
        )
        if case.entity_type == "firm":
            firm = self.economy.firms[case.entity_id]
            firm.status = "dissolved"
            firm.dissolved_tick = tick
            for employment in sorted(
                self.economy.employments.values(),
                key=lambda row: row.employment_id,
            ):
                if employment.firm_id != case.entity_id or employment.ended_tick is not None:
                    continue
                employment.ended_tick = tick
                self.population[employment.agent_id].employment_status = "unemployed"
                events.append(
                    emit(
                        NewEvent(
                            FIRED,
                            {
                                "employment_id": employment.employment_id,
                                "agent_id": employment.agent_id,
                                "firm_id": case.entity_id,
                                "reason": "firm_exit",
                                "severance_cents": 0,
                                "notice_ticks": 0,
                            },
                            actor_id=case.entity_id,
                            subject_ids=(employment.agent_id,),
                        )
                    )
                )
            firm.headcount = 0
            for vacancy in sorted(
                self.economy.vacancies.values(),
                key=lambda row: row.vacancy_id,
            ):
                if vacancy.firm_id != case.entity_id or vacancy.status != "open":
                    continue
                vacancy.status = "closed"
                events.append(
                    emit(
                        NewEvent(
                            VACANCY_CLOSED,
                            {
                                "vacancy_id": vacancy.vacancy_id,
                                "reason": "firm_exit",
                                "applicants_n": vacancy.applicants_n,
                                "days_open": (tick - vacancy.posted_tick)
                                // max(1, self.settings.clock.ticks_per_sim_day),
                            },
                            actor_id=case.entity_id,
                        )
                    )
                )
            for offer in sorted(
                self.economy.offers.values(),
                key=lambda row: row.offer_id,
            ):
                if offer.firm_id != case.entity_id or offer.status != "open":
                    continue
                offer.status = "expired"
                events.append(
                    emit(
                        NewEvent(
                            OFFER_EXPIRED,
                            {
                                "offer_id": offer.offer_id,
                                "agent_id": offer.agent_id,
                            },
                            actor_id=case.entity_id,
                            subject_ids=(offer.agent_id,),
                        )
                    )
                )
            for security in list(self.economy.exchange.securities.values()):
                if security.issuer_firm_id == case.entity_id and security.status == "listed":
                    security.last_price_cents = 0
                    events.extend(
                        self.exchange.delist(
                            security.symbol,
                            "bankruptcy",
                            tick,
                            emit,
                        )
                    )
            for startup in self.economy.ventures.startups.values():
                if startup.firm_id == case.entity_id:
                    startup.status = "dead"
                    events.append(
                        emit(
                            NewEvent(
                                STARTUP_DIED,
                                {
                                    "startup_id": startup.startup_id,
                                    "cause": "bankruptcy",
                                    "age_ticks": tick - startup.founded_tick,
                                    "total_raised_cents": startup.total_raised_cents,
                                    "investors_loss_cents": sum(
                                        row.invested_cents
                                        for row in self._cap_rows(case.entity_id)
                                        if row.holder_id != startup.founder_id
                                    ),
                                },
                                subject_ids=(case.entity_id,),
                            )
                        )
                    )
        elif case.entity_type == "agent":
            expires = tick + (
                self.settings.bankruptcy.credit_flag_years
                * self.settings.clock.days_per_sim_year
                * self.settings.clock.ticks_per_sim_day
            )
            self.economy.ventures.credit_flags_until_tick[case.entity_id] = expires
            events.append(
                emit(
                    NewEvent(
                        CREDIT_FLAG_SET,
                        {
                            "entity_id": case.entity_id,
                            "flag": "bankruptcy",
                            "set_tick": tick,
                            "expires_tick": expires,
                        },
                        subject_ids=(case.entity_id,),
                    )
                )
            )
        return tuple(events)

    def _declare_dividend(
        self,
        action: Action,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        firm_id = str(action.params.get("firm_id", ""))
        firm = self.economy.firms.get(firm_id)
        total = int(action.params.get("total_cents", 0))
        if (
            firm is None
            or firm.founder_id != action.actor_id
            or total <= 0
            or self.economy.ledger.balance(firm.ledger_account_id) < total
        ):
            return ()
        cap_rows = [row for row in self._cap_rows(firm_id) if row.holder_id != "option_pool"]
        distribution = allocate(
            total,
            [(row.holder_id, row.shares) for row in cap_rows if row.shares > 0],
        )
        declared = emit(
            NewEvent(
                DIVIDEND_DECLARED,
                {
                    "firm_id": firm_id,
                    "per_share_cents": total // max(1, sum(row.shares for row in cap_rows)),
                    "total_cents": total,
                    "record_tick": tick,
                    "payable_tick": tick,
                    "decided_by": action.actor_id,
                },
                actor_id=action.actor_id,
                subject_ids=(firm_id,),
            )
        )
        events: list[Event] = [declared]
        for holder_id, cents in sorted(distribution.items()):
            if cents <= 0:
                continue
            shares = sum(row.shares for row in cap_rows if row.holder_id == holder_id)
            expected = self.economy.ledger.next_txn_id(tick)
            paid = emit(
                NewEvent(
                    DIVIDEND_PAID,
                    {
                        "firm_id": firm_id,
                        "holder_id": holder_id,
                        "shares": shares,
                        "cents": cents,
                        "txn_id": str(expected),
                    },
                    subject_ids=(firm_id, holder_id),
                )
            )
            txn_id = self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(
                    firm.ledger_account_id,
                    self._deposit(holder_id),
                    cents,
                    "dividend",
                ),
                tick=tick,
                cause=paid,
            )
            if txn_id != expected:
                raise RuntimeError("dividend ledger ordinal diverged")
            events.append(paid)
        paid_total = sum(distribution.values())
        by_firm = self.economy.ventures.dividends_by_tick.setdefault(tick, {})
        by_firm[firm_id] = by_firm.get(firm_id, 0) + paid_total
        return tuple(events)

    def _runway_step(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.settings.ventures.enabled:
            return ()
        if tick % self.settings.clock.ticks_per_sim_day != 0:
            return ()
        events: list[Event] = []
        for startup in sorted(
            self.economy.ventures.startups.values(),
            key=lambda row: row.startup_id,
        ):
            if startup.status != "active":
                continue
            liquid = max(0, self.economy.ledger.liquid(startup.firm_id))
            startup.runway_ticks = (
                liquid * self.settings.clock.ticks_per_sim_day // max(1, startup.burn_rate_cents)
            )
            firm = self.economy.firms[startup.firm_id]
            startup.revenue_ttm_cents = firm.cumulative_revenue_cents
            events.append(
                emit(
                    NewEvent(
                        RUNWAY_UPDATED,
                        {
                            "startup_id": startup.startup_id,
                            "liquid_cents": liquid,
                            "burn_rate_cents": startup.burn_rate_cents,
                            "runway_ticks": startup.runway_ticks,
                            "stage": startup.stage,
                            "revenue_ttm_cents": startup.revenue_ttm_cents,
                        },
                        subject_ids=(startup.firm_id,),
                    )
                )
            )
        return tuple(events)

    def _solvency_step(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.settings.bankruptcy.enabled:
            return ()
        events: list[Event] = []
        persistence = (
            self.settings.bankruptcy.insolvency_persist_days * self.settings.clock.ticks_per_sim_day
        )
        for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
            if firm.status != "active" or firm.firm_id == "fm_broker":
                self.economy.ventures.insolvency_since_tick.pop(firm.firm_id, None)
                continue
            if self._firm_balance_sheet_net_worth(firm.firm_id) < 0:
                since = self.economy.ventures.insolvency_since_tick.setdefault(
                    firm.firm_id,
                    tick,
                )
                if tick - since >= persistence:
                    events.extend(
                        self._file_case(
                            firm.firm_id,
                            "balance_sheet",
                            tick,
                            emit,
                        )
                    )
            else:
                self.economy.ventures.insolvency_since_tick.pop(firm.firm_id, None)
        return tuple(events)

    def _firm_balance_sheet_net_worth(self, firm_id: str) -> int:
        listed = sum(
            holding.qty * security.last_price_cents
            for holding in self.economy.exchange.holdings.values()
            if holding.holder_id == firm_id
            and holding.qty > 0
            and (security := self.economy.exchange.securities.get(holding.symbol)) is not None
            and security.status == "listed"
        )
        inventory = sum(
            row.quantity * row.unit_cost_cents
            for row in self.economy.inventory.values()
            if row.firm_id == firm_id and row.quantity > 0
        )
        unlisted = sum(
            max(0, row.invested_cents)
            for row in self.economy.ventures.cap_table.values()
            if row.holder_id == firm_id
            and row.firm_id != firm_id
            and self._listed_symbol(row.firm_id) is None
        )
        assets = (
            max(0, self.economy.ledger.liquid(firm_id))
            + listed
            + inventory
            + max(0, self.economy.firms[firm_id].capital_cents)
            + unlisted
        )
        liabilities = sum(
            row.outstanding_cents
            for row in self.economy.loans.values()
            if row.borrower_id == firm_id and row.status not in {"repaid", "written_off"}
        )
        return assets - liabilities

    def _fund_step(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        if not self.settings.ventures.enabled:
            return ()
        quarter = 90 * self.settings.clock.ticks_per_sim_day
        if tick <= 0 or tick % quarter != 0:
            return ()
        events: list[Event] = []
        for fund in sorted(self.economy.ventures.funds.values(), key=lambda row: row.fund_id):
            fee = fund.committed_cents * fund.management_fee_bp // 10_000 // 4
            source = self._deposit(fund.firm_id)
            destination = self._deposit(fund.gp_agent_id)
            if fee <= 0 or self.economy.ledger.balance(source) < fee:
                continue
            expected = self.economy.ledger.next_txn_id(tick)
            event = emit(
                NewEvent(
                    MANAGEMENT_FEE_CHARGED,
                    {
                        "fund_id": fund.fund_id,
                        "cents": fee,
                        "period": tick // quarter,
                        "txn_id": str(expected),
                    },
                    subject_ids=(fund.firm_id, fund.gp_agent_id),
                )
            )
            txn_id = self.economy.ledger.post_transaction(
                self.economy.ledger.transfer(
                    source,
                    destination,
                    fee,
                    "transfer",
                ),
                tick=tick,
                cause=event,
            )
            if txn_id != expected:
                raise RuntimeError("management-fee ledger ordinal diverged")
            events.append(event)
        return tuple(events)

    def distribute_fund_exit(
        self,
        fund_id: str,
        source_exit_id: str,
        gross_cents: int,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        fund = self.economy.ventures.funds[fund_id]
        source = self._deposit(fund.firm_id)
        if gross_cents <= 0 or self.economy.ledger.balance(source) < gross_cents:
            return ()
        hurdle = fund.called_cents * (10_000 + fund.hurdle_bp) // 10_000
        carry = (
            max(0, gross_cents - hurdle) * fund.carry_bp // 10_000 if gross_cents > hurdle else 0
        )
        lp_pool = gross_cents - carry
        lp_distribution = allocate(lp_pool, list(sorted(fund.lps.items())))
        expected = self.economy.ledger.next_txn_id(tick)
        event = emit(
            NewEvent(
                FUND_DISTRIBUTION,
                {
                    "fund_id": fund_id,
                    "source_exit_id": source_exit_id,
                    "gross_cents": gross_cents,
                    "lp_cents": lp_pool,
                    "carry_cents": carry,
                    "hurdle_met": gross_cents > hurdle,
                    "txn_id": str(expected),
                },
                subject_ids=(fund.firm_id,),
            )
        )
        legs: list[Leg] = []
        for lp_id, cents in sorted(lp_distribution.items()):
            if cents:
                legs.extend(
                    self.economy.ledger.transfer(
                        source,
                        self._deposit(lp_id),
                        cents,
                        "dividend",
                    )
                )
        if carry:
            legs.extend(
                self.economy.ledger.transfer(
                    source,
                    self._deposit(fund.gp_agent_id),
                    carry,
                    "dividend",
                )
            )
        txn_id = self.economy.ledger.post_transaction(
            _coalesce(legs),
            tick=tick,
            cause=event,
        )
        if txn_id != expected:
            raise RuntimeError("fund distribution ledger ordinal diverged")
        distributions = self.economy.ventures.fund_distributions_cents
        distributions[fund_id] = distributions.get(fund_id, 0) + gross_cents
        return (event,)

    def _expire_term_sheets(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for term in sorted(
            self.economy.ventures.term_sheets.values(),
            key=lambda row: row.term_sheet_id,
        ):
            if term.status != "open" or tick <= term.expires_tick:
                continue
            term.status = "expired"
            events.append(
                emit(
                    NewEvent(
                        TERM_SHEET_EXPIRED,
                        {"term_sheet_id": term.term_sheet_id},
                        subject_ids=(term.startup_id, term.investor_id),
                    )
                )
            )
        return tuple(events)

    def _cap_event(
        self,
        row: CapTableState,
        before: int,
        cause: str,
        emit: Emit,
    ) -> Event:
        return emit(
            NewEvent(
                CAP_TABLE_UPDATED,
                {
                    "firm_id": row.firm_id,
                    "holder_id": row.holder_id,
                    "share_class": row.share_class,
                    "shares_before": before,
                    "shares_after": row.shares,
                    "cause": cause,
                    "fully_diluted_after": self.economy.ventures.shares(row.firm_id),
                },
                subject_ids=(row.firm_id, row.holder_id),
            )
        )

    def _cap_rows(self, firm_id: str) -> list[CapTableState]:
        return sorted(
            (row for row in self.economy.ventures.cap_table.values() if row.firm_id == firm_id),
            key=lambda row: (row.share_class, row.holder_id),
        )

    def _traction(self, startup: StartupState) -> Mapping[str, Any]:
        firm = self.economy.firms[startup.firm_id]
        rounds = [
            row
            for row in self.economy.ventures.rounds.values()
            if row.startup_id == startup.startup_id
        ]
        return {
            "revenue_ttm_cents": firm.cumulative_revenue_cents,
            "revenue_growth_bp": 0,
            "headcount": firm.headcount,
            "burn_rate_cents": startup.burn_rate_cents,
            "runway_ticks": startup.runway_ticks,
            "months_since_founding": 12
            * (max(0, self.population.tick - startup.founded_tick))
            // max(
                1,
                self.settings.clock.days_per_sim_year * self.settings.clock.ticks_per_sim_day,
            ),
            "prior_rounds": [row.round_id for row in rounds],
        }

    @staticmethod
    def _next_stage(stage: str) -> str:
        stages = ("pre_seed", "seed", "series_a", "series_b", "growth")
        try:
            return stages[min(len(stages) - 1, stages.index(stage) + 1)]
        except ValueError:
            return "seed"

    def _deposit(self, owner_id: str) -> str:
        deposits = [
            account
            for account in self.economy.ledger.accounts_of(owner_id)
            if parse_account_id(account)[0] == "dep"
        ]
        if not deposits:
            raise LedgerError(f"owner has no deposit account: {owner_id}")
        return deposits[0]

    def _action_under_stay(self, action: Action) -> bool:
        candidates = {action.actor_id}
        for key in ("entity_id", "firm_id", "acquirer_id", "target_id"):
            value = action.params.get(key)
            if value:
                candidates.add(str(value))
        return any(
            case.status == "open" and case.entity_id in candidates
            for case in self.economy.ventures.bankruptcies.values()
        )
