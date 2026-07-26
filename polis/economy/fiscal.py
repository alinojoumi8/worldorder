from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from polis.agents.state import AgentPopulation
from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.credit import CreditContext
from polis.economy.labour import labour_force
from polis.economy.ledger import Leg, bank_of, parse_account_id
from polis.economy.money import allocate, bp, mint
from polis.economy.state import (
    BondState,
    EconomyState,
    LoanState,
    TaxAssessmentState,
)
from polis.events.kinds import (
    BOND_AUCTION_CLEARED,
    BOND_AUCTION_FAILED,
    BOND_ISSUED,
    BOND_MATURED,
    COUPON_PAID,
    GOV_BUDGET_CLOSED,
    TAX_ARREARS,
    TAX_ASSESSED,
    TAX_COLLECTED,
    TRANSFER_PAID,
)
from polis.events.types import Event, NewEvent

Emit = Callable[[NewEvent], Event]


@dataclass(frozen=True, slots=True)
class FiscalContext:
    settings: Settings
    population: AgentPopulation
    economy: EconomyState

    @property
    def ticks_per_day(self) -> int:
        return self.settings.clock.ticks_per_sim_day

    @property
    def ticks_per_year(self) -> int:
        return self.settings.clock.days_per_sim_year * self.ticks_per_day


def treasury_account(economy: EconomyState) -> str:
    return next(
        account_id
        for account_id in economy.ledger.accounts_of("gv_treasury")
        if parse_account_id(account_id)[0] == "dep" and parse_account_id(account_id)[2] == "bk_cb"
    )


def _owner_deposit(owner_id: str, economy: EconomyState) -> str | None:
    values = [
        account_id
        for account_id in economy.ledger.accounts_of(owner_id)
        if parse_account_id(account_id)[0] == "dep"
    ]
    return sorted(values)[0] if values else None


def government_transfer_legs(
    recipient_deposit: str,
    cents: int,
    economy: EconomyState,
) -> tuple[Leg, ...]:
    source = treasury_account(economy)
    destination_bank = bank_of(recipient_deposit)
    if destination_bank == "bk_cb":
        return (
            Leg(source, -1, cents, "transfer"),
            Leg(recipient_deposit, 1, cents, "transfer"),
        )
    if destination_bank is None:
        raise ValueError("government transfers require a deposit recipient")
    bank = economy.banks[destination_bank]
    return (
        Leg(source, -1, cents, "transfer"),
        Leg(bank.reserve_account_id, 1, cents, "transfer"),
        Leg(bank.deposit_liability_account_id, -1, cents, "transfer"),
        Leg(recipient_deposit, 1, cents, "transfer"),
    )


def tax_collection_legs(
    taxpayer_deposit: str,
    cents: int,
    economy: EconomyState,
) -> tuple[Leg, ...]:
    destination = treasury_account(economy)
    source_bank = bank_of(taxpayer_deposit)
    if source_bank == "bk_cb":
        return (
            Leg(taxpayer_deposit, -1, cents, "tax"),
            Leg(destination, 1, cents, "tax"),
        )
    if source_bank is None:
        raise ValueError("tax collection requires a deposit source")
    bank = economy.banks[source_bank]
    return (
        Leg(taxpayer_deposit, -1, cents, "tax"),
        Leg(bank.deposit_liability_account_id, 1, cents, "tax"),
        Leg(bank.reserve_account_id, -1, cents, "tax"),
        Leg(destination, 1, cents, "tax"),
    )


def assess_taxes(
    tick: int,
    *,
    ctx: FiscalContext,
    emit: Emit,
) -> tuple[Event, ...]:
    quarter = 90 * ctx.ticks_per_day
    if tick % quarter != 0:
        return ()
    events: list[Event] = []
    for firm in sorted(ctx.economy.firms.values(), key=lambda row: row.firm_id):
        prior_revenue = ctx.economy.treasury.corporate_revenue_marks.get(firm.firm_id, 0)
        prior_wages = ctx.economy.treasury.corporate_wage_marks.get(firm.firm_id, 0)
        revenue = max(0, firm.cumulative_revenue_cents - prior_revenue)
        wages = max(0, firm.cumulative_wage_cents - prior_wages)
        profit = max(0, revenue - wages)
        assessed = bp(profit, ctx.settings.treasury.tax.corporate_bp)
        ctx.economy.treasury.corporate_revenue_marks[firm.firm_id] = firm.cumulative_revenue_cents
        ctx.economy.treasury.corporate_wage_marks[firm.firm_id] = firm.cumulative_wage_cents
        if assessed <= 0:
            continue
        assessment_id = mint("tax", tick, len(ctx.economy.tax_assessments))
        due_tick = tick + 7 * ctx.ticks_per_day
        ctx.economy.tax_assessments[assessment_id] = TaxAssessmentState(
            assessment_id,
            firm.firm_id,
            "corporate",
            profit,
            ctx.settings.treasury.tax.corporate_bp,
            assessed,
            tick,
            due_tick,
        )
        events.append(
            emit(
                NewEvent(
                    TAX_ASSESSED,
                    {
                        "taxpayer_id": firm.firm_id,
                        "tax_type": "corporate",
                        "base_cents": profit,
                        "rate_bp": ctx.settings.treasury.tax.corporate_bp,
                        "assessed_cents": assessed,
                        "period": f"quarter:{tick // quarter}",
                        "due_tick": due_tick,
                        "assessment_id": assessment_id,
                    },
                    subject_ids=(firm.firm_id,),
                )
            )
        )
    return tuple(events)


def collect_taxes(
    tick: int,
    *,
    ctx: FiscalContext,
    emit: Emit,
) -> tuple[Event, ...]:
    events: list[Event] = []
    for assessment in sorted(
        ctx.economy.tax_assessments.values(),
        key=lambda row: row.assessment_id,
    ):
        if assessment.status != "assessed":
            continue
        due = assessment.assessed_cents - assessment.paid_cents
        source = _owner_deposit(assessment.taxpayer_id, ctx.economy)
        if source is None or due <= 0:
            continue
        available = ctx.economy.ledger.liquid(assessment.taxpayer_id)
        payment = min(due, available)
        if payment <= 0:
            continue
        expected = ctx.economy.ledger.next_txn_id(tick)
        event = emit(
            NewEvent(
                TAX_COLLECTED,
                {
                    "taxpayer_id": assessment.taxpayer_id,
                    "tax_type": assessment.tax_type,
                    "cents": payment,
                    "txn_id": str(expected),
                    "assessment_id": assessment.assessment_id,
                },
                actor_id=assessment.taxpayer_id,
            )
        )
        transaction_id = ctx.economy.ledger.post_transaction(
            tax_collection_legs(source, payment, ctx.economy),
            tick=tick,
            cause=event,
        )
        if transaction_id != expected:
            raise RuntimeError("tax collection transaction ordinal diverged")
        assessment.paid_cents += payment
        if assessment.paid_cents == assessment.assessed_cents:
            assessment.status = "paid"
        treasury = ctx.economy.treasury
        treasury.receipts_cents += payment
        treasury.period_receipts_cents += payment
        events.append(event)
    return tuple(events)


def convert_arrears(
    tick: int,
    *,
    ctx: FiscalContext,
    emit: Emit,
) -> tuple[Event, ...]:
    events: list[Event] = []
    receivable = next(
        (
            account_id
            for account_id in ctx.economy.ledger.accounts_of("gv_treasury")
            if parse_account_id(account_id)[0] == "txr"
        ),
        None,
    )
    if receivable is None:
        receivable = ctx.economy.ledger.open_account(
            "txr",
            "gv_treasury",
            "government",
            tick=tick,
        )
    for assessment in sorted(
        ctx.economy.tax_assessments.values(),
        key=lambda row: row.assessment_id,
    ):
        if assessment.status != "assessed" or tick < assessment.due_tick:
            continue
        amount = assessment.assessed_cents - assessment.paid_cents
        if amount <= 0:
            assessment.status = "paid"
            continue
        loan_id = mint("txln", tick, len(ctx.economy.loans))
        borrower_type = "firm" if assessment.taxpayer_id in ctx.economy.firms else "agent"
        payable = ctx.economy.ledger.open_account(
            "lnp",
            assessment.taxpayer_id,
            borrower_type,
            ref=loan_id,
            tick=tick,
        )
        expected = ctx.economy.ledger.next_txn_id(tick)
        event = emit(
            NewEvent(
                TAX_ARREARS,
                {
                    "taxpayer_id": assessment.taxpayer_id,
                    "cents": amount,
                    "loan_id": loan_id,
                    "penalty_rate_bp": ctx.settings.treasury.tax.arrears_penalty_bp,
                    "txn_id": str(expected),
                },
                actor_id=assessment.taxpayer_id,
            )
        )
        transaction_id = ctx.economy.ledger.post_transaction(
            (
                Leg(receivable, 1, amount, "tax"),
                Leg(payable, -1, amount, "tax"),
            ),
            tick=tick,
            cause=event,
        )
        if transaction_id != expected:
            raise RuntimeError("tax arrears transaction ordinal diverged")
        term = ctx.settings.credit.max_term_days["tax_arrears"] * ctx.ticks_per_day
        ctx.economy.loans[loan_id] = LoanState(
            loan_id,
            "gv_treasury",
            assessment.taxpayer_id,
            "tax_arrears",
            amount,
            amount,
            ctx.settings.treasury.tax.arrears_penalty_bp,
            term,
            tick,
            tick + term,
            "current",
            {"assessment_id": assessment.assessment_id},
            0,
            10_000,
            receivable,
            payable,
            amount,
            1,
            tick + term,
        )
        assessment.status = "arrears"
        events.append(event)
    return tuple(events)


@mechanism(
    "spend.unemployment_benefit",
    entails=(
        "Agents in measured unemployment with prior employment receive positive income. "
        "This damps demand contractions by construction; downturn claims must report the "
        "replacement rate and include the zero-benefit ablation."
    ),
    config_key="mechanisms.spend_unemployment_benefit",
)
def pay_transfers(
    tick: int,
    *,
    ctx: FiscalContext,
    emit: Emit,
) -> tuple[Event, ...]:
    cadence = 7 * ctx.ticks_per_day
    if tick % cadence != 0:
        return ()
    if (
        ctx.settings.mechanisms.get("spend_unemployment_benefit", "on") == "off"
        or ctx.settings.treasury.spend.benefit_replacement_bp <= 0
    ):
        return ()
    force = labour_force(
        ctx.population,
        ctx.economy,
        tick=tick,
        search_window_ticks=28 * ctx.ticks_per_day,
        retirement_age=ctx.settings.labour.retirement_age,
    )
    events: list[Event] = []
    treasury = ctx.economy.treasury
    source = treasury_account(ctx.economy)
    for agent_id in force.unemployed:
        prior = sorted(
            (
                employment
                for employment in ctx.economy.employments.values()
                if employment.agent_id == agent_id and employment.ended_tick is not None
            ),
            key=lambda row: row.ended_tick or -1,
            reverse=True,
        )
        if not prior:
            continue
        weekly = bp(
            prior[0].wage_cents // 2,
            ctx.settings.treasury.spend.benefit_replacement_bp,
        )
        destination = _owner_deposit(agent_id, ctx.economy)
        if weekly <= 0 or destination is None:
            continue
        expected = ctx.economy.ledger.next_txn_id(tick)
        event = emit(
            NewEvent(
                TRANSFER_PAID,
                {
                    "recipient_id": agent_id,
                    "programme": "unemployment",
                    "cents": weekly,
                    "txn_id": str(expected),
                },
                subject_ids=(agent_id,),
            )
        )
        transaction_id = ctx.economy.ledger.post_transaction(
            government_transfer_legs(destination, weekly, ctx.economy),
            tick=tick,
            cause=event,
            allow_negative=frozenset({source}),
        )
        if transaction_id != expected:
            raise RuntimeError("government transfer transaction ordinal diverged")
        treasury.spending_cents += weekly
        treasury.period_spending_cents += weekly
        events.append(event)
    return tuple(events)


def pay_coupons_and_maturities(
    tick: int,
    *,
    ctx: FiscalContext,
    emit: Emit,
) -> tuple[Event, ...]:
    events: list[Event] = []
    treasury = treasury_account(ctx.economy)
    semiannual = 180 * ctx.ticks_per_day
    for bond in sorted(ctx.economy.bonds.values(), key=lambda row: row.symbol):
        if bond.status != "outstanding":
            continue
        holder_bank = ctx.economy.banks[bond.holder_id]
        if tick < bond.matures_tick and tick - bond.last_coupon_tick >= semiannual:
            coupon = bp(bond.face_cents, bond.coupon_bp) // 2
            if coupon:
                expected = ctx.economy.ledger.next_txn_id(tick)
                event = emit(
                    NewEvent(
                        COUPON_PAID,
                        {
                            "symbol": bond.symbol,
                            "holders_n": 1,
                            "total_cents": coupon,
                            "txn_id": str(expected),
                        },
                        subject_ids=(bond.holder_id,),
                    )
                )
                ctx.economy.ledger.post_transaction(
                    (
                        Leg(treasury, -1, coupon, "interest"),
                        Leg(holder_bank.reserve_account_id, 1, coupon, "interest"),
                    ),
                    tick=tick,
                    cause=event,
                    allow_negative=frozenset({treasury}),
                )
                bond.last_coupon_tick = tick
                ctx.economy.treasury.debt_service_cents += coupon
                ctx.economy.treasury.period_debt_service_cents += coupon
                events.append(event)
        if tick >= bond.matures_tick:
            expected = ctx.economy.ledger.next_txn_id(tick)
            event = emit(
                NewEvent(
                    BOND_MATURED,
                    {
                        "symbol": bond.symbol,
                        "face_cents": bond.face_cents,
                        "holders_n": 1,
                        "txn_id": str(expected),
                    },
                    subject_ids=(bond.holder_id,),
                )
            )
            ctx.economy.ledger.post_transaction(
                (
                    Leg(treasury, -1, bond.face_cents, "transfer"),
                    Leg(holder_bank.reserve_account_id, 1, bond.face_cents, "transfer"),
                ),
                tick=tick,
                cause=event,
                allow_negative=frozenset({treasury}),
            )
            bond.status = "matured"
            ctx.economy.bond_holdings_cents[bond.holder_id].pop(bond.symbol, None)
            events.append(event)
    return tuple(events)


def finance_deficit(
    tick: int,
    *,
    ctx: FiscalContext,
    emit: Emit,
) -> tuple[Event, ...]:
    treasury = treasury_account(ctx.economy)
    shortfall = ctx.settings.treasury.floor_cents - ctx.economy.ledger.balance(treasury)
    if shortfall <= 0:
        return ()
    denomination = ctx.settings.treasury.bond_denomination_cents
    face = ((shortfall + denomination - 1) // denomination) * denomination
    banks = [
        bank
        for bank in ctx.economy.banks.values()
        if not bank.is_central and bank.status == "active"
    ]
    capacity = {
        bank.bank_id: max(0, ctx.economy.ledger.balance(bank.reserve_account_id)) for bank in banks
    }
    available = sum(capacity.values())
    auction_id = mint("auc", tick, len(ctx.economy.bonds))
    symbol = f"POLG{tick:06d}-{len(ctx.economy.bonds) + 1:04d}"
    if available < face:
        return (
            emit(
                NewEvent(
                    BOND_AUCTION_FAILED,
                    {
                        "auction_id": auction_id,
                        "offered_cents": face,
                        "bid_cents": available,
                        "shortfall_cents": face - available,
                    },
                )
            ),
        )
    allocations = allocate(face, tuple(sorted(capacity.items())))
    holder_id = max(allocations, key=lambda bank_id: (allocations[bank_id], bank_id))
    # A single registered security is held by the largest primary-auction participant.
    # Other bank allocations are represented as separate same-coupon tranches.
    events: list[Event] = []
    term_days = ctx.settings.treasury.bond_terms_days[
        len(ctx.economy.bonds) % len(ctx.settings.treasury.bond_terms_days)
    ]
    coupon = ctx.economy.policy_rate_bp + ctx.settings.treasury.sovereign_spread_bp
    issued = emit(
        NewEvent(
            BOND_ISSUED,
            {
                "symbol": symbol,
                "face_cents": face,
                "coupon_bp": coupon,
                "matures_tick": tick + term_days * ctx.ticks_per_day,
                "auction_id": auction_id,
            },
        )
    )
    events.append(issued)
    legs: list[Leg] = [Leg(treasury, 1, face, "transfer")]
    for bank_id, cents in sorted(allocations.items()):
        if cents:
            legs.append(
                Leg(
                    ctx.economy.banks[bank_id].reserve_account_id,
                    -1,
                    cents,
                    "transfer",
                )
            )
    expected = ctx.economy.ledger.next_txn_id(tick)
    cleared = emit(
        NewEvent(
            BOND_AUCTION_CLEARED,
            {
                "auction_id": auction_id,
                "offered_cents": face,
                "bid_cents": face,
                "clearing_yield_bp": coupon,
                "allocations": allocations,
                "txn_id": str(expected),
            },
        )
    )
    transaction_id = ctx.economy.ledger.post_transaction(
        tuple(legs),
        tick=tick,
        cause=cleared,
    )
    if transaction_id != expected:
        raise RuntimeError("bond auction transaction ordinal diverged")
    events.append(cleared)
    # Persist one tranche per actual holder so maturity and capital marks are exact.
    for ordinal, (bank_id, cents) in enumerate(sorted(allocations.items())):
        if cents <= 0:
            continue
        tranche_symbol = symbol if bank_id == holder_id else f"{symbol}-{ordinal}"
        ctx.economy.bonds[tranche_symbol] = BondState(
            tranche_symbol,
            cents,
            coupon,
            tick,
            tick + term_days * ctx.ticks_per_day,
            bank_id,
            last_coupon_tick=tick,
        )
        ctx.economy.bond_holdings_cents.setdefault(bank_id, {})[tranche_symbol] = cents
    return tuple(events)


def close_budget(
    tick: int,
    *,
    ctx: FiscalContext,
    emit: Emit,
) -> tuple[Event, ...]:
    quarter = 90 * ctx.ticks_per_day
    if tick % quarter != 0:
        return ()
    treasury = ctx.economy.treasury
    balance = (
        treasury.period_receipts_cents
        - treasury.period_spending_cents
        - treasury.period_debt_service_cents
    )
    debt = sum(
        bond.face_cents for bond in ctx.economy.bonds.values() if bond.status == "outstanding"
    )
    event = emit(
        NewEvent(
            GOV_BUDGET_CLOSED,
            {
                "period": f"quarter:{tick // quarter}",
                "receipts_cents": treasury.period_receipts_cents,
                "spending_cents": treasury.period_spending_cents,
                "debt_service_cents": treasury.period_debt_service_cents,
                "balance_cents": balance,
                "debt_cents": debt,
                "debt_to_gdp_bp": 0,
            },
        )
    )
    treasury.period_receipts_cents = 0
    treasury.period_spending_cents = 0
    treasury.period_debt_service_cents = 0
    return (event,)


def fiscal_step(
    tick: int,
    *,
    ctx: FiscalContext,
    credit: CreditContext,
    emit: Emit,
) -> tuple[Event, ...]:
    if credit.economy is not ctx.economy:
        raise ValueError("fiscal and credit contexts must share an economy")
    events: list[Event] = []
    events.extend(pay_coupons_and_maturities(tick, ctx=ctx, emit=emit))
    events.extend(assess_taxes(tick, ctx=ctx, emit=emit))
    events.extend(collect_taxes(tick, ctx=ctx, emit=emit))
    events.extend(convert_arrears(tick, ctx=ctx, emit=emit))
    events.extend(pay_transfers(tick, ctx=ctx, emit=emit))
    events.extend(finance_deficit(tick, ctx=ctx, emit=emit))
    events.extend(close_budget(tick, ctx=ctx, emit=emit))
    return tuple(events)
