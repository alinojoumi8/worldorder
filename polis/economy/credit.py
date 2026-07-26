from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import TYPE_CHECKING, Any, Literal

from polis.agents.state import AgentPopulation
from polis.config.canon import canonical_json
from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.ledger import Leg, parse_account_id
from polis.economy.money import MONEY_CTX, bp, mint
from polis.economy.state import (
    BankState,
    EconomyState,
    LoanPaymentState,
    LoanState,
)
from polis.events.kinds import (
    INTEREST_ACCRUED,
    LOAN_DEFAULTED,
    LOAN_DELINQUENT,
    LOAN_ORIGINATED,
    LOAN_PAYMENT_MADE,
    LOAN_PAYMENT_MISSED,
    LOAN_REPAID,
    LOAN_WRITTEN_OFF,
)
from polis.events.types import Event, NewEvent
from polis.llm.purposes import Purpose as LlmPurpose

if TYPE_CHECKING:
    from polis.llm.router import LLMRouter

Purpose = Literal[
    "consumer",
    "mortgage",
    "corporate",
    "interbank",
    "sovereign",
    "tax_arrears",
]
Emit = Callable[[NewEvent], Event]

CREDIT_EVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approve": {"type": "boolean"},
        "rate_view_bp": {"type": "integer", "minimum": 0, "maximum": 10_000},
        "amount_view_cents": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10_000_000_000,
        },
        "concerns": {
            "type": "array",
            "items": {"type": "string", "maxLength": 80},
            "maxItems": 8,
        },
    },
    "required": ["approve", "rate_view_bp", "amount_view_cents", "concerns"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class LoanRequest:
    borrower_id: str
    lender_id: str
    principal_cents: int
    purpose: Purpose
    term_ticks: int
    collateral: Mapping[str, object]
    collateral_value_cents: int
    stated_purpose_text: str | None = None


@dataclass(frozen=True, slots=True)
class BorrowerState:
    employed_ticks_last_year: int
    annual_income_cents: int
    annual_debt_service_cents: int
    on_time_payments: int
    total_payments: int
    total_debt_cents: int
    total_assets_cents: int
    bankruptcy_flag: bool
    delinquency_flag: bool


@dataclass(frozen=True, slots=True)
class MarketState:
    median_wage_cents: int
    ticks_per_sim_year: int


@dataclass(frozen=True, slots=True)
class LoanDecision:
    approved: bool
    score_bp: int
    components: Mapping[str, int]
    offered_cents: int
    annual_rate_bp: int
    term_ticks: int
    reason_codes: tuple[str, ...]
    llm_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class Payment:
    number: int
    principal_cents: int
    interest_cents: int
    payment_cents: int
    outstanding_after_cents: int


@dataclass(frozen=True, slots=True)
class CreditContext:
    settings: Settings
    population: AgentPopulation
    economy: EconomyState

    @property
    def ticks_per_year(self) -> int:
        return self.settings.clock.days_per_sim_year * self.settings.clock.ticks_per_sim_day

    @property
    def payment_interval_ticks(self) -> int:
        return self.settings.credit.payment_interval_days * self.settings.clock.ticks_per_sim_day


@mechanism(
    "credit_scoring",
    entails=(
        "Credit access is weakly increasing in employment stability, income, and repayment "
        "history, and weakly decreasing in debt-to-income, leverage, and a prior bankruptcy. "
        "The offered rate is weakly decreasing in the score. Therefore any cross-sectional "
        "finding that the unemployed, the indebted, or the previously bankrupt are denied "
        "credit or charged more is implied and is not a result. Credit volume, clustering, "
        "and shock amplification through the capital constraint remain outcomes."
    ),
    config_key="mechanisms.credit_scoring",
)
def credit_score_bp(
    borrower: BorrowerState,
    request: LoanRequest,
    market: MarketState,
) -> tuple[int, Mapping[str, int]]:
    income_stability = (
        min(
            10_000,
            10_000 * borrower.employed_ticks_last_year // max(1, market.ticks_per_sim_year),
        )
        * min(
            10_000,
            10_000 * borrower.annual_income_cents // max(1, market.median_wage_cents),
        )
        // 10_000
    )
    dti = min(
        10_000,
        10_000 * borrower.annual_debt_service_cents // max(1, borrower.annual_income_cents),
    )
    history = 10_000 * (borrower.on_time_payments + 1) // (borrower.total_payments + 2)
    leverage = min(
        10_000,
        10_000 * borrower.total_debt_cents // max(1, borrower.total_assets_cents),
    )
    coverage = min(
        10_000,
        10_000 * request.collateral_value_cents // max(1, request.principal_cents),
    )
    raw = (
        3_000 * income_stability
        + 2_500 * (10_000 - dti)
        + 2_000 * history
        + 1_500 * (10_000 - leverage)
        + 1_000 * coverage
    ) // 10_000
    if borrower.bankruptcy_flag:
        raw = raw * 5_000 // 10_000
    if borrower.delinquency_flag:
        raw = raw * 7_500 // 10_000
    return max(0, min(10_000, raw)), {
        "income_stability": income_stability,
        "dti": dti,
        "history": history,
        "leverage": leverage,
        "coverage": coverage,
    }


def deposits_cents(bank_id: str, economy: EconomyState) -> int:
    bank = economy.banks[bank_id]
    return max(0, -economy.ledger.balance(bank.deposit_liability_account_id))


def reserves_cents(bank_id: str, economy: EconomyState) -> int:
    return economy.ledger.balance(economy.banks[bank_id].reserve_account_id)


def capital_cents(bank_id: str, economy: EconomyState) -> int:
    holdings = sum(economy.bond_holdings_cents.get(bank_id, {}).values())
    return economy.ledger.net_worth(bank_id) + holdings


def rwa_cents(bank_id: str, ctx: CreditContext) -> int:
    result = 0
    for loan in ctx.economy.loans.values():
        if loan.lender_id != bank_id or loan.status not in {"current", "delinquent", "default"}:
            continue
        weight = ctx.settings.credit.risk_weight_bp.get(loan.purpose, 10_000)
        result += bp(loan.outstanding_cents, weight)
    return result


def capital_ratio_bp(bank_id: str, ctx: CreditContext) -> int:
    capital = capital_cents(bank_id, ctx.economy)
    rwa = rwa_cents(bank_id, ctx)
    if rwa <= 0:
        return 10_000 if capital >= 0 else -10_000
    return 10_000 * capital // rwa


def borrower_state(borrower_id: str, ctx: CreditContext) -> BorrowerState:
    ticks_per_year = ctx.ticks_per_year
    employment = next(
        (
            row
            for row in ctx.economy.employments.values()
            if row.agent_id == borrower_id and row.ended_tick is None
        ),
        None,
    )
    if borrower_id in ctx.economy.firms:
        firm = ctx.economy.firms[borrower_id]
        annual_income = max(
            ctx.settings.economy.median_wage_cents,
            ctx.settings.economy.median_wage_cents * max(1, firm.target_headcount),
            firm.cumulative_revenue_cents,
        )
        employed_ticks = ticks_per_year
        assets = max(1, ctx.economy.ledger.net_worth(borrower_id) + firm.capital_cents)
    else:
        annual_income = employment.wage_cents * 24 if employment is not None else 0
        employed_ticks = ticks_per_year if employment is not None else 0
        assets = max(1, ctx.economy.ledger.net_worth(borrower_id))
    loans = [
        row
        for row in ctx.economy.loans.values()
        if row.borrower_id == borrower_id and row.status in {"current", "delinquent", "default"}
    ]
    payments = [
        row
        for row in ctx.economy.loan_payments
        if ctx.economy.loans[row.loan_id].borrower_id == borrower_id
    ]
    return BorrowerState(
        employed_ticks,
        annual_income,
        sum(loan.payment_cents * 12 for loan in loans),
        sum(not row.missed for row in payments),
        len(payments),
        sum(loan.outstanding_cents for loan in loans),
        assets,
        False,
        any(loan.status in {"delinquent", "default"} for loan in loans),
    )


@mechanism(
    "credit_supply",
    entails=(
        "Endogenous credit is limited by borrower score, bank capital, reserves, and "
        "concentration. The exogenous ablation removes bank-side quantity constraints while "
        "retaining borrower underwriting."
    ),
    config_key="mechanisms.credit_supply",
)
def decide(
    request: LoanRequest,
    borrower: BorrowerState,
    bank: BankState,
    *,
    ctx: CreditContext,
) -> LoanDecision:
    market = MarketState(ctx.settings.economy.median_wage_cents, ctx.ticks_per_year)
    score, components = credit_score_bp(borrower, request, market)
    term_cap = (
        ctx.settings.credit.max_term_days.get(request.purpose, request.term_ticks)
        * ctx.settings.clock.ticks_per_sim_day
    )
    term_ticks = max(ctx.payment_interval_ticks, min(request.term_ticks, term_cap))
    income_cap = bp(
        borrower.annual_income_cents,
        ctx.settings.credit.max_loan_income_multiple_bp,
    )
    collateral_cap = (
        request.collateral_value_cents
        if request.collateral_value_cents > 0
        else request.principal_cents
    )
    offered = min(request.principal_cents, income_cap, collateral_cap)
    bank_capital = capital_cents(bank.bank_id, ctx.economy)
    ratio = capital_ratio_bp(bank.bank_id, ctx)
    stressed = ratio < ctx.settings.banking.capital_buffer_bp
    score_floor = ctx.settings.credit.min_score_bp + (
        ctx.settings.banking.stress_score_bump_bp if stressed else 0
    )
    reasons: list[str] = []
    if score < score_floor:
        reasons.append("credit_score")
    if offered <= 0:
        reasons.append("amount_cap")
    exogenous = ctx.settings.mechanisms.get("credit_supply", "endogenous") == "exogenous"
    existing = sum(
        loan.outstanding_cents
        for loan in ctx.economy.loans.values()
        if loan.lender_id == bank.bank_id
        and loan.borrower_id == request.borrower_id
        and loan.status in {"current", "delinquent", "default"}
    )
    if not exogenous:
        if bank.lending_frozen or ratio < ctx.settings.banking.capital_ratio_min_bp:
            reasons.append("capital_ratio")
        post_deposits = deposits_cents(bank.bank_id, ctx.economy) + offered
        if reserves_cents(bank.bank_id, ctx.economy) < bp(
            post_deposits,
            bank.reserve_ratio_bp,
        ):
            reasons.append("reserve_ratio")
        concentration_cap = bp(bank_capital, ctx.settings.credit.concentration_bp)
        if existing + offered > max(0, concentration_cap):
            reasons.append("concentration")
    risk_gap = 10_000 - score
    risk_spread = ctx.settings.credit.risk_spread_k * risk_gap * risk_gap // 100_000_000
    term_years = max(1, term_ticks // max(1, ctx.ticks_per_year))
    annual_rate = (
        ctx.economy.policy_rate_bp
        + ctx.settings.credit.base_spread_bp
        + risk_spread
        + term_years * ctx.settings.credit.term_premium_bp_per_year
    )
    return LoanDecision(
        not reasons,
        score,
        components,
        offered if not reasons else 0,
        annual_rate,
        term_ticks,
        tuple(sorted(set(reasons))),
    )


async def decide_with_underwriting(
    request: LoanRequest,
    borrower: BorrowerState,
    bank: BankState,
    *,
    tick: int,
    ctx: CreditContext,
    router: LLMRouter,
) -> LoanDecision:
    """Run the optional CREDIT_EVAL ablation without bypassing bank constraints."""
    scorecard = decide(request, borrower, bank, ctx=ctx)
    if ctx.settings.banking.underwriting == "scorecard":
        return scorecard

    prompt = canonical_json(
        {
            "instruction": (
                "Assess this loan using only the supplied simulation state. Return the "
                "requested structured decision without naming a provider or system."
            ),
            "request": asdict(request),
            "borrower": asdict(borrower),
            "scorecard_reference": {
                "approved": scorecard.approved,
                "score_bp": scorecard.score_bp,
                "components": dict(scorecard.components),
                "offered_cents": scorecard.offered_cents,
                "annual_rate_bp": scorecard.annual_rate_bp,
                "reason_codes": list(scorecard.reason_codes),
            },
            "bank_constraints": {
                "capital_ratio_bp": capital_ratio_bp(bank.bank_id, ctx),
                "minimum_capital_ratio_bp": ctx.settings.banking.capital_ratio_min_bp,
                "reserve_cents": reserves_cents(bank.bank_id, ctx.economy),
                "deposit_cents": deposits_cents(bank.bank_id, ctx.economy),
                "lending_frozen": bank.lending_frozen,
            },
            "stated_purpose": request.stated_purpose_text,
        }
    )
    result = await router.call(
        LlmPurpose.CREDIT_EVAL,
        request.borrower_id,
        tick,
        {
            "system": "You are a bank credit officer operating inside a simulation.",
            "prompt": prompt,
        },
        CREDIT_EVAL_SCHEMA,
    )
    call_id = str(result.call_id)
    if result.degraded or not result.parsed_ok or result.parsed is None:
        return LoanDecision(
            False,
            scorecard.score_bp,
            scorecard.components,
            0,
            scorecard.annual_rate_bp,
            scorecard.term_ticks,
            tuple(sorted(set((*scorecard.reason_codes, "llm_unavailable")))),
            call_id,
        )

    hard_reasons = tuple(
        reason
        for reason in scorecard.reason_codes
        if reason in {"amount_cap", "capital_ratio", "reserve_ratio", "concentration"}
    )
    parsed = result.parsed
    approved = bool(parsed["approve"]) and not hard_reasons
    income_cap = bp(
        borrower.annual_income_cents,
        ctx.settings.credit.max_loan_income_multiple_bp,
    )
    collateral_cap = (
        request.collateral_value_cents
        if request.collateral_value_cents > 0
        else request.principal_cents
    )
    capacity = min(request.principal_cents, income_cap, collateral_cap)
    amount_view = max(0, int(parsed["amount_view_cents"]))
    offered = min(capacity, amount_view) if approved else 0
    if offered <= 0:
        approved = False
    reasons = list(hard_reasons)
    if not bool(parsed["approve"]):
        reasons.append("llm_denied")
    if offered <= 0 and "amount_cap" not in reasons:
        reasons.append("amount_cap")
    annual_rate = max(
        ctx.economy.policy_rate_bp,
        min(10_000, int(parsed["rate_view_bp"])),
    )
    return LoanDecision(
        approved,
        scorecard.score_bp,
        scorecard.components,
        offered,
        annual_rate,
        scorecard.term_ticks,
        tuple(sorted(set(reasons))),
        call_id,
    )


def schedule(
    principal_cents: int,
    annual_rate_bp: int,
    term_ticks: int,
    interval_ticks: int,
    *,
    ticks_per_year: int = 360,
) -> tuple[Payment, ...]:
    if principal_cents <= 0 or term_ticks <= 0 or interval_ticks <= 0:
        raise ValueError("principal, term, and interval must be positive")
    periods = max(1, (term_ticks + interval_ticks - 1) // interval_ticks)
    with localcontext(MONEY_CTX):
        periodic_rate = (
            Decimal(annual_rate_bp)
            / Decimal(10_000)
            * Decimal(interval_ticks)
            / Decimal(ticks_per_year)
        )
        if periodic_rate > 0:
            raw_payment = (
                Decimal(principal_cents)
                * periodic_rate
                / (Decimal(1) - (Decimal(1) + periodic_rate) ** Decimal(-periods))
            )
        else:
            raw_payment = Decimal(principal_cents) / Decimal(periods)
        level_payment = int(raw_payment.to_integral_value(rounding=ROUND_CEILING))
        outstanding = principal_cents
        result: list[Payment] = []
        for number in range(1, periods + 1):
            interest = int(
                (Decimal(outstanding) * periodic_rate).to_integral_value(rounding=ROUND_FLOOR)
            )
            principal = (
                outstanding
                if number == periods
                else min(
                    outstanding,
                    max(1, level_payment - interest),
                )
            )
            outstanding -= principal
            result.append(
                Payment(
                    number,
                    principal,
                    interest,
                    principal + interest,
                    outstanding,
                )
            )
            if outstanding == 0:
                break
        return tuple(result)


def _borrower_deposit(request: LoanRequest, tick: int, ctx: CreditContext) -> str:
    candidates = [
        account_id
        for account_id in ctx.economy.ledger.accounts_of(request.borrower_id)
        if parse_account_id(account_id)[0] == "dep"
        and parse_account_id(account_id)[2] == request.lender_id
    ]
    if not candidates:
        owner_type = "firm" if request.borrower_id in ctx.economy.firms else "agent"
        return ctx.economy.ledger.open_account(
            "dep",
            request.borrower_id,
            owner_type,
            bank_id=request.lender_id,
            tick=tick,
        )
    return sorted(candidates)[0]


def originate(
    request: LoanRequest,
    decision: LoanDecision,
    tick: int,
    *,
    ctx: CreditContext,
    emit: Emit,
) -> tuple[Event, ...]:
    if not decision.approved or decision.offered_cents <= 0:
        return ()
    ordinal = sum(loan.originated_tick == tick for loan in ctx.economy.loans.values())
    loan_id = mint("ln", tick, ordinal)
    lender_receivable = ctx.economy.ledger.open_account(
        "lnr",
        request.lender_id,
        "bank",
        ref=loan_id,
        tick=tick,
    )
    borrower_type = "firm" if request.borrower_id in ctx.economy.firms else "agent"
    borrower_payable = ctx.economy.ledger.open_account(
        "lnp",
        request.borrower_id,
        borrower_type,
        ref=loan_id,
        tick=tick,
    )
    borrower_deposit = _borrower_deposit(request, tick, ctx)
    bank = ctx.economy.banks[request.lender_id]
    payments = schedule(
        decision.offered_cents,
        decision.annual_rate_bp,
        decision.term_ticks,
        ctx.payment_interval_ticks,
        ticks_per_year=ctx.ticks_per_year,
    )
    expected = ctx.economy.ledger.next_txn_id(tick)
    event = emit(
        NewEvent(
            LOAN_ORIGINATED,
            {
                "loan_id": loan_id,
                "lender_id": request.lender_id,
                "borrower_id": request.borrower_id,
                "principal_cents": decision.offered_cents,
                "annual_rate_bp": decision.annual_rate_bp,
                "term_ticks": decision.term_ticks,
                "payment_cents": payments[0].payment_cents,
                "payments_n": len(payments),
                "collateral": dict(request.collateral),
                "credit_score_bp": decision.score_bp,
                "txn_id": str(expected),
            },
            actor_id=request.borrower_id,
            subject_ids=(request.lender_id,),
        )
    )
    transaction_id = ctx.economy.ledger.post_transaction(
        (
            Leg(lender_receivable, 1, decision.offered_cents, "loan"),
            Leg(
                bank.deposit_liability_account_id,
                -1,
                decision.offered_cents,
                "loan",
            ),
            Leg(borrower_deposit, 1, decision.offered_cents, "loan"),
            Leg(borrower_payable, -1, decision.offered_cents, "loan"),
        ),
        tick=tick,
        cause=event,
    )
    if transaction_id != expected:
        raise RuntimeError("loan origination transaction ordinal diverged")
    ctx.economy.loans[loan_id] = LoanState(
        loan_id,
        request.lender_id,
        request.borrower_id,
        request.purpose,
        decision.offered_cents,
        decision.offered_cents,
        decision.annual_rate_bp,
        decision.term_ticks,
        tick,
        tick + decision.term_ticks,
        "current",
        dict(request.collateral),
        request.collateral_value_cents,
        decision.score_bp,
        lender_receivable,
        borrower_payable,
        payments[0].payment_cents,
        len(payments),
        tick + ctx.payment_interval_ticks,
    )
    return (event,)


class CreditEngine:
    def __init__(self, ctx: CreditContext) -> None:
        self.ctx = ctx

    def accrue_interest(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        if tick % self.ctx.settings.clock.ticks_per_sim_day != 0:
            return ()
        events: list[Event] = []
        denominator = 10_000 * self.ctx.settings.clock.days_per_sim_year
        for loan in sorted(self.ctx.economy.loans.values(), key=lambda row: row.loan_id):
            if loan.status not in {"current", "delinquent"} or loan.outstanding_cents <= 0:
                continue
            if any(
                case.entity_id == loan.borrower_id and case.status == "open"
                for case in self.ctx.economy.ventures.bankruptcies.values()
            ):
                continue
            numerator = loan.outstanding_cents * loan.annual_rate_bp + loan.accrual_remainder
            accrued = numerator // denominator
            loan.accrual_remainder = numerator % denominator
            loan.accrued_interest_cents += accrued
            events.append(
                emit(
                    NewEvent(
                        INTEREST_ACCRUED,
                        {
                            "loan_id": loan.loan_id,
                            "cents": accrued,
                            "annual_rate_bp": loan.annual_rate_bp,
                            "period_ticks": self.ctx.settings.clock.ticks_per_sim_day,
                            "accrued_total_cents": loan.accrued_interest_cents,
                        },
                        subject_ids=(loan.borrower_id, loan.lender_id),
                    )
                )
            )
        return tuple(events)

    def amortise(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        for loan in sorted(self.ctx.economy.loans.values(), key=lambda row: row.loan_id):
            if loan.status not in {"current", "delinquent"} or tick < loan.next_payment_tick:
                continue
            if any(
                case.entity_id == loan.borrower_id and case.status == "open"
                for case in self.ctx.economy.ventures.bankruptcies.values()
            ):
                continue
            if loan.borrower_id in self.ctx.economy.banks or (
                loan.lender_id not in self.ctx.economy.banks and loan.lender_id != "gv_treasury"
            ):
                continue
            interest = loan.accrued_interest_cents
            final = loan.payments_made + 1 >= loan.payments_n or tick >= loan.matures_tick
            principal = (
                loan.outstanding_cents
                if final
                else min(loan.outstanding_cents, max(1, loan.payment_cents - interest))
            )
            due = principal + interest
            borrower_deposits = [
                account_id
                for account_id in self.ctx.economy.ledger.accounts_of(loan.borrower_id)
                if parse_account_id(account_id)[0] == "dep"
            ]
            available = (
                max(
                    (
                        self.ctx.economy.ledger.balance(account_id)
                        for account_id in borrower_deposits
                    ),
                    default=0,
                )
                if loan.lender_id == "gv_treasury"
                else self.ctx.economy.ledger.liquid(loan.borrower_id)
            )
            payment_id = mint("lpay", tick, len(self.ctx.economy.loan_payments))
            if available < due:
                if loan.missed_since_tick is None:
                    loan.missed_since_tick = tick
                days_past_due = (
                    tick - loan.missed_since_tick
                ) // self.ctx.settings.clock.ticks_per_sim_day
                event = emit(
                    NewEvent(
                        LOAN_PAYMENT_MISSED,
                        {
                            "loan_id": loan.loan_id,
                            "due_cents": due,
                            "available_cents": available,
                            "days_past_due": days_past_due,
                        },
                        actor_id=loan.borrower_id,
                        subject_ids=(loan.lender_id,),
                    )
                )
                events.append(event)
                self.ctx.economy.loan_payments.append(
                    LoanPaymentState(payment_id, loan.loan_id, tick, 0, interest, True)
                )
                loan.next_payment_tick += self.ctx.payment_interval_ticks
                events.extend(self._transition_missed(loan, tick, emit))
                continue
            if loan.lender_id == "gv_treasury":
                borrower_deposit = next(
                    account_id
                    for account_id in sorted(borrower_deposits)
                    if self.ctx.economy.ledger.balance(account_id) >= due
                )
            else:
                borrower_deposit = next(
                    account_id
                    for account_id in borrower_deposits
                    if parse_account_id(account_id)[2] == loan.lender_id
                )
            expected = self.ctx.economy.ledger.next_txn_id(tick)
            event = emit(
                NewEvent(
                    LOAN_PAYMENT_MADE,
                    {
                        "loan_id": loan.loan_id,
                        "payment_no": loan.payments_made + 1,
                        "principal_cents": principal,
                        "interest_cents": interest,
                        "outstanding_after_cents": loan.outstanding_cents - principal,
                        "txn_id": str(expected),
                    },
                    actor_id=loan.borrower_id,
                    subject_ids=(loan.lender_id,),
                )
            )
            if loan.lender_id == "gv_treasury":
                source_bank_id = parse_account_id(borrower_deposit)[2]
                if source_bank_id is None:
                    raise RuntimeError("tax arrears require a banked deposit")
                source_bank = self.ctx.economy.banks[source_bank_id]
                treasury = next(
                    account_id
                    for account_id in self.ctx.economy.ledger.accounts_of("gv_treasury")
                    if parse_account_id(account_id)[0] == "dep"
                )
                legs: tuple[Leg, ...] = (
                    Leg(borrower_deposit, -1, due, "tax"),
                    Leg(source_bank.deposit_liability_account_id, 1, due, "tax"),
                    Leg(source_bank.reserve_account_id, -1, due, "tax"),
                    Leg(treasury, 1, due, "tax"),
                    Leg(loan.lender_receivable_account_id, -1, principal, "loan"),
                    Leg(loan.borrower_payable_account_id, 1, principal, "loan"),
                )
            else:
                bank = self.ctx.economy.banks[loan.lender_id]
                legs = (
                    Leg(borrower_deposit, -1, due, "loan"),
                    Leg(bank.deposit_liability_account_id, 1, due, "loan"),
                    Leg(loan.lender_receivable_account_id, -1, principal, "loan"),
                    Leg(loan.borrower_payable_account_id, 1, principal, "loan"),
                )
            transaction_id = self.ctx.economy.ledger.post_transaction(
                legs,
                tick=tick,
                cause=event,
            )
            if transaction_id != expected:
                raise RuntimeError("loan payment transaction ordinal diverged")
            loan.outstanding_cents -= principal
            loan.total_interest_paid_cents += interest
            loan.total_interest_scheduled_cents += interest
            loan.accrued_interest_cents = 0
            loan.payments_made += 1
            loan.missed_since_tick = None
            loan.next_payment_tick += self.ctx.payment_interval_ticks
            self.ctx.economy.loan_payments.append(
                LoanPaymentState(payment_id, loan.loan_id, tick, principal, interest, False)
            )
            events.append(event)
            if loan.outstanding_cents == 0:
                loan.status = "repaid"
                loan.closed_tick = tick
                events.append(
                    emit(
                        NewEvent(
                            LOAN_REPAID,
                            {
                                "loan_id": loan.loan_id,
                                "total_interest_cents": loan.total_interest_paid_cents,
                                "ticks_to_repay": tick - loan.originated_tick,
                                "early": tick < loan.matures_tick,
                            },
                            actor_id=loan.borrower_id,
                            subject_ids=(loan.lender_id,),
                        )
                    )
                )
        return tuple(events)

    def _transition_missed(self, loan: LoanState, tick: int, emit: Emit) -> tuple[Event, ...]:
        if loan.missed_since_tick is None:
            return ()
        days = (tick - loan.missed_since_tick) // self.ctx.settings.clock.ticks_per_sim_day
        if loan.status == "current" and days >= self.ctx.settings.credit.delinquency_days:
            capitalised = loan.accrued_interest_cents
            expected = self.ctx.economy.ledger.next_txn_id(tick)
            event = emit(
                NewEvent(
                    LOAN_DELINQUENT,
                    {
                        "loan_id": loan.loan_id,
                        "days_past_due": days,
                        "capitalised_interest_cents": capitalised,
                        "txn_id": str(expected),
                    },
                    actor_id=loan.borrower_id,
                    subject_ids=(loan.lender_id,),
                )
            )
            if capitalised:
                transaction_id = self.ctx.economy.ledger.post_transaction(
                    (
                        Leg(
                            loan.lender_receivable_account_id,
                            1,
                            capitalised,
                            "interest",
                        ),
                        Leg(
                            loan.borrower_payable_account_id,
                            -1,
                            capitalised,
                            "interest",
                        ),
                    ),
                    tick=tick,
                    cause=event,
                )
                if transaction_id != expected:
                    raise RuntimeError("interest capitalisation ordinal diverged")
                loan.outstanding_cents += capitalised
                loan.total_interest_scheduled_cents += capitalised
                loan.capitalised_interest_cents += capitalised
                loan.accrued_interest_cents = 0
            loan.status = "delinquent"
            loan.annual_rate_bp += self.ctx.settings.credit.delinquency_penalty_bp
            return (event,)
        if loan.status == "delinquent" and days >= self.ctx.settings.credit.default_days:
            return self.mark_default(loan.loan_id, "dpd", tick, emit)
        return ()

    def mark_default(
        self,
        loan_id: str,
        trigger: str,
        tick: int,
        emit: Emit,
    ) -> tuple[Event, ...]:
        loan = self.ctx.economy.loans[loan_id]
        if loan.status in {"default", "repaid", "written_off"}:
            return ()
        loan.status = "default"
        loan.defaulted_tick = tick
        return (
            emit(
                NewEvent(
                    LOAN_DEFAULTED,
                    {
                        "loan_id": loan.loan_id,
                        "outstanding_cents": loan.outstanding_cents,
                        "trigger": trigger,
                    },
                    actor_id=loan.borrower_id,
                    subject_ids=(loan.lender_id,),
                )
            ),
        )

    def write_off_due(self, tick: int, emit: Emit) -> tuple[Event, ...]:
        events: list[Event] = []
        threshold = (
            self.ctx.settings.credit.writeoff_after_days * self.ctx.settings.clock.ticks_per_sim_day
        )
        for loan in sorted(self.ctx.economy.loans.values(), key=lambda row: row.loan_id):
            if (
                loan.status == "default"
                and loan.defaulted_tick is not None
                and tick - loan.defaulted_tick >= threshold
            ):
                events.extend(
                    write_off_loan(
                        loan.loan_id,
                        loan.outstanding_cents,
                        0,
                        tick,
                        ctx=self.ctx,
                        emit=emit,
                    )
                )
        return tuple(events)


def write_off_loan(
    loan_id: str,
    amount_cents: int,
    recovery_cents: int,
    tick: int,
    *,
    ctx: CreditContext,
    emit: Emit,
) -> tuple[Event, ...]:
    loan = ctx.economy.loans[loan_id]
    written_off = min(amount_cents, loan.outstanding_cents)
    if written_off <= 0:
        return ()
    expected = ctx.economy.ledger.next_txn_id(tick)
    event = emit(
        NewEvent(
            LOAN_WRITTEN_OFF,
            {
                "loan_id": loan_id,
                "written_off_cents": written_off,
                "recovery_cents": recovery_cents,
                "loss_given_default_bp": (
                    10_000 - 10_000 * recovery_cents // max(1, written_off + recovery_cents)
                ),
                "txn_id": str(expected),
            },
            actor_id=loan.lender_id,
            subject_ids=(loan.borrower_id,),
        )
    )
    transaction_id = ctx.economy.ledger.post_transaction(
        (
            Leg(loan.lender_receivable_account_id, -1, written_off, "write_off"),
            Leg(loan.borrower_payable_account_id, 1, written_off, "write_off"),
        ),
        tick=tick,
        cause=event,
    )
    if transaction_id != expected:
        raise RuntimeError("loan write-off transaction ordinal diverged")
    loan.outstanding_cents -= written_off
    loan.total_interest_scheduled_cents += loan.accrued_interest_cents
    loan.total_interest_forgiven_cents += (
        loan.capitalised_interest_cents + loan.accrued_interest_cents
    )
    loan.accrued_interest_cents = 0
    if loan.outstanding_cents == 0:
        loan.status = "written_off"
        loan.closed_tick = tick
    return (event,)
