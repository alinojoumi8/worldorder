from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.credit import (
    CreditContext,
    capital_cents,
    capital_ratio_bp,
    deposits_cents,
    reserves_cents,
    rwa_cents,
    write_off_loan,
)
from polis.economy.fiscal import treasury_account
from polis.economy.ledger import Leg, parse_account_id
from polis.economy.money import allocate, bp, mint
from polis.economy.state import EconomyState, LoanState
from polis.events.kinds import (
    BANK_FAILED,
    BANK_RATIOS_COMPUTED,
    BANK_UNDERCAPITALISED,
    DEPOSIT_HAIRCUT,
    DEPOSIT_INSURANCE_PAID,
    DISCOUNT_WINDOW_BORROWED,
    INTERBANK_LOAN,
    INTERBANK_REFUSED,
    MONEY_ISSUED,
    POLICY_RATE_SET,
)
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry

Emit = Callable[[NewEvent], Event]


@dataclass(frozen=True, slots=True)
class CentralContext:
    settings: Settings
    economy: EconomyState
    rng: RngRegistry


def _credit_context(ctx: CentralContext, credit: CreditContext) -> CreditContext:
    if credit.economy is not ctx.economy:
        raise ValueError("central and credit contexts must share an economy")
    return credit


def apply_pending_policy(tick: int, economy: EconomyState) -> None:
    pending = economy.pending_policy_rate
    if pending is not None and pending[0] <= tick:
        economy.policy_rate_bp = pending[1]
        economy.pending_policy_rate = None


@mechanism(
    "banking.policy_rate_rule",
    entails=(
        "Under the Taylor rule the policy rate is an increasing function of inflation and "
        "the output gap by construction; those correlations are not findings. Transmission "
        "to employment, consumption, and defaults remains an outcome."
    ),
    config_key="mechanisms.banking_policy_rate_rule",
)
def policy_rate_target_bp(
    *,
    current_bp: int,
    inflation_bp: int,
    output_gap_bp: int,
    settings: Settings,
) -> tuple[int, str]:
    rule = settings.banking.policy_rate_rule
    if rule in {"fixed", "political"}:
        return settings.banking.policy_rate_bp, rule
    taylor = settings.banking.taylor
    raw = (
        taylor.neutral_bp
        + taylor.phi_pi_bp * (inflation_bp - taylor.target_bp) // 10_000
        + taylor.phi_y_bp * output_gap_bp // 10_000
    )
    return max(taylor.bounds_bp[0], min(taylor.bounds_bp[1], raw)), "rule"


def set_policy_rate(
    tick: int,
    *,
    ctx: CentralContext,
    emit: Emit,
) -> tuple[Event, ...]:
    cadence = ctx.settings.banking.policy_review_days * ctx.settings.clock.ticks_per_sim_day
    if tick % max(1, cadence) != 0:
        return ()
    year = ctx.settings.clock.days_per_sim_year * ctx.settings.clock.ticks_per_sim_day
    current_cpi = ctx.economy.cpi_history_bp.get(tick, 10_000)
    prior_cpi = ctx.economy.cpi_history_bp.get(tick - year)
    inflation = (
        0
        if prior_cpi is None or prior_cpi <= 0
        else 10_000 * (current_cpi - prior_cpi) // prior_cpi
    )
    target, setter = policy_rate_target_bp(
        current_bp=ctx.economy.policy_rate_bp,
        inflation_bp=inflation,
        output_gap_bp=0,
        settings=ctx.settings,
    )
    previous = ctx.economy.policy_rate_bp
    ctx.economy.pending_policy_rate = (tick + 1, target)
    return (
        emit(
            NewEvent(
                POLICY_RATE_SET,
                {
                    "rate_bp": target,
                    "prev_rate_bp": previous,
                    "setter": setter,
                    "inflation_bp": inflation,
                    "output_gap_bp": 0,
                    "effective_tick": tick + 1,
                },
            )
        ),
    )


def _originate_interbank(
    lender_id: str,
    borrower_id: str,
    cents: int,
    tick: int,
    *,
    ctx: CentralContext,
    credit: CreditContext,
    emit: Emit,
) -> tuple[Event, ...]:
    ordinal = sum(loan.originated_tick == tick for loan in ctx.economy.loans.values())
    loan_id = mint("ib", tick, ordinal)
    receivable = ctx.economy.ledger.open_account(
        "lnr",
        lender_id,
        "bank",
        ref=loan_id,
        tick=tick,
    )
    payable = ctx.economy.ledger.open_account(
        "lnp",
        borrower_id,
        "bank",
        ref=loan_id,
        tick=tick,
    )
    rate = ctx.economy.policy_rate_bp + ctx.settings.banking.interbank_spread_bp
    event = emit(
        NewEvent(
            INTERBANK_LOAN,
            {
                "loan_id": loan_id,
                "lender_bank_id": lender_id,
                "borrower_bank_id": borrower_id,
                "cents": cents,
                "rate_bp": rate,
                "term_ticks": ctx.settings.clock.ticks_per_sim_day,
            },
            actor_id=lender_id,
            subject_ids=(borrower_id,),
        )
    )
    ctx.economy.ledger.post_transaction(
        (
            Leg(ctx.economy.banks[lender_id].reserve_account_id, -1, cents, "loan"),
            Leg(ctx.economy.banks[borrower_id].reserve_account_id, 1, cents, "loan"),
            Leg(receivable, 1, cents, "loan"),
            Leg(payable, -1, cents, "loan"),
        ),
        tick=tick,
        cause=event,
    )
    ctx.economy.loans[loan_id] = LoanState(
        loan_id,
        lender_id,
        borrower_id,
        "interbank",
        cents,
        cents,
        rate,
        ctx.settings.clock.ticks_per_sim_day,
        tick,
        tick + ctx.settings.clock.ticks_per_sim_day,
        "current",
        {},
        0,
        10_000,
        receivable,
        payable,
        cents,
        1,
        tick + ctx.settings.clock.ticks_per_sim_day,
    )
    return (event,)


def discount_window(
    bank_id: str,
    shortfall_cents: int,
    tick: int,
    *,
    ctx: CentralContext,
    emit: Emit,
) -> tuple[Event, ...]:
    if shortfall_cents <= 0:
        return ()
    ordinal = sum(loan.originated_tick == tick for loan in ctx.economy.loans.values())
    loan_id = mint("dw", tick, ordinal)
    receivable = ctx.economy.ledger.open_account(
        "lnr",
        "bk_cb",
        "central_bank",
        ref=loan_id,
        tick=tick,
    )
    payable = ctx.economy.ledger.open_account(
        "lnp",
        bank_id,
        "bank",
        ref=loan_id,
        tick=tick,
    )
    issuance = next(
        account_id
        for account_id in ctx.economy.ledger.accounts_of("bk_cb")
        if parse_account_id(account_id)[0] == "iss"
    )
    expected = ctx.economy.ledger.next_txn_id(tick)
    issued = emit(
        NewEvent(
            MONEY_ISSUED,
            {
                "amount_cents": shortfall_cents,
                "recipient_account_id": ctx.economy.banks[bank_id].reserve_account_id,
                "instrument": "reserves",
                "purpose": "discount_window",
                "txn_id": str(expected),
            },
            subject_ids=(bank_id,),
        )
    )
    transaction_id = ctx.economy.ledger.issue_base_money(
        (
            Leg(
                ctx.economy.banks[bank_id].reserve_account_id,
                1,
                shortfall_cents,
                "issuance",
            ),
            Leg(issuance, -1, shortfall_cents, "issuance"),
            Leg(receivable, 1, shortfall_cents, "issuance"),
            Leg(payable, -1, shortfall_cents, "issuance"),
        ),
        tick=tick,
        cause=issued,
    )
    if transaction_id != expected:
        raise RuntimeError("discount-window transaction ordinal diverged")
    rate = ctx.economy.policy_rate_bp + ctx.settings.banking.discount_penalty_bp
    ctx.economy.loans[loan_id] = LoanState(
        loan_id,
        "bk_cb",
        bank_id,
        "interbank",
        shortfall_cents,
        shortfall_cents,
        rate,
        30 * ctx.settings.clock.ticks_per_sim_day,
        tick,
        tick + 30 * ctx.settings.clock.ticks_per_sim_day,
        "current",
        {},
        0,
        10_000,
        receivable,
        payable,
        shortfall_cents,
        1,
        tick + 30 * ctx.settings.clock.ticks_per_sim_day,
    )
    borrowed = emit(
        NewEvent(
            DISCOUNT_WINDOW_BORROWED,
            {
                "bank_id": bank_id,
                "cents": shortfall_cents,
                "penalty_rate_bp": ctx.settings.banking.discount_penalty_bp,
                "reserve_shortfall_cents": shortfall_cents,
            },
            subject_ids=(bank_id,),
        )
    )
    return issued, borrowed


def resolve_failure(
    bank_id: str,
    tick: int,
    *,
    ctx: CentralContext,
    credit: CreditContext,
    emit: Emit,
) -> tuple[Event, ...]:
    bank = ctx.economy.banks[bank_id]
    if bank.is_central or bank.status == "failed":
        return ()
    candidates = [
        row
        for row in ctx.economy.banks.values()
        if not row.is_central and row.bank_id != bank_id and row.status == "active"
    ]
    resolution = ctx.settings.banking.resolution
    if resolution == "assume" and not candidates:
        return ()
    primary_buyer = max(
        candidates or [ctx.economy.banks["bk_cb"]],
        key=lambda row: (
            capital_cents(row.bank_id, ctx.economy),
            row.bank_id,
        ),
    )
    capital = capital_cents(bank_id, ctx.economy)
    deposits = deposits_cents(bank_id, ctx.economy)
    failed_event = emit(
        NewEvent(
            BANK_FAILED,
            {
                "bank_id": bank_id,
                "capital_cents": capital,
                "deposits_cents": deposits,
                "shortfall_cents": max(0, -capital),
                "resolution": resolution,
                (
                    "assuming_bank_id" if resolution == "assume" else "primary_buyer_bank_id"
                ): primary_buyer.bank_id,
            },
            subject_ids=(bank_id, primary_buyer.bank_id),
        )
    )
    events: list[Event] = [failed_event]

    for loan in sorted(ctx.economy.loans.values(), key=lambda row: row.loan_id):
        if loan.lender_id != bank_id or loan.status in {"repaid", "written_off"}:
            continue
        if loan.status in {"delinquent", "default"}:
            events.extend(
                write_off_loan(
                    loan.loan_id,
                    loan.outstanding_cents,
                    0,
                    tick,
                    ctx=credit,
                    emit=emit,
                )
            )

    if resolution == "liquidate":
        issuance = next(
            account_id
            for account_id in ctx.economy.ledger.accounts_of("bk_cb")
            if parse_account_id(account_id)[0] == "iss"
        )
        for loan in sorted(ctx.economy.loans.values(), key=lambda row: row.loan_id):
            if loan.lender_id != bank_id or loan.status != "current":
                continue
            sale_price = bp(
                loan.outstanding_cents,
                ctx.settings.banking.fire_sale_bp,
            )
            funded_buyers = [
                row for row in candidates if reserves_cents(row.bank_id, ctx.economy) >= sale_price
            ]
            buyer = max(
                funded_buyers or [ctx.economy.banks["bk_cb"]],
                key=lambda row: (
                    capital_cents(row.bank_id, ctx.economy),
                    row.bank_id,
                ),
            )
            new_receivable = ctx.economy.ledger.open_account(
                "lnr",
                buyer.bank_id,
                "central_bank" if buyer.is_central else "bank",
                ref=loan.loan_id,
                tick=tick,
            )
            if buyer.is_central:
                expected = ctx.economy.ledger.next_txn_id(tick)
                intervention = emit(
                    NewEvent(
                        MONEY_ISSUED,
                        {
                            "amount_cents": sale_price,
                            "recipient_account_id": bank.reserve_account_id,
                            "instrument": "loan_purchase",
                            "purpose": "bank_resolution",
                            "txn_id": str(expected),
                        },
                        subject_ids=(bank_id, loan.borrower_id),
                    )
                )
                transaction_id = ctx.economy.ledger.issue_base_money(
                    (
                        Leg(
                            loan.lender_receivable_account_id,
                            -1,
                            loan.outstanding_cents,
                            "loan",
                        ),
                        Leg(
                            new_receivable,
                            1,
                            loan.outstanding_cents,
                            "loan",
                        ),
                        Leg(bank.reserve_account_id, 1, sale_price, "issuance"),
                        Leg(issuance, -1, sale_price, "issuance"),
                    ),
                    tick=tick,
                    cause=intervention,
                )
                if transaction_id != expected:
                    raise RuntimeError("bank-resolution issuance ordinal diverged")
                events.append(intervention)
            else:
                ctx.economy.ledger.post_transaction(
                    (
                        Leg(
                            loan.lender_receivable_account_id,
                            -1,
                            loan.outstanding_cents,
                            "loan",
                        ),
                        Leg(
                            new_receivable,
                            1,
                            loan.outstanding_cents,
                            "loan",
                        ),
                        Leg(buyer.reserve_account_id, -1, sale_price, "transfer"),
                        Leg(bank.reserve_account_id, 1, sale_price, "transfer"),
                    ),
                    tick=tick,
                    cause=failed_event,
                )
            loan.lender_id = buyer.bank_id
            loan.lender_receivable_account_id = new_receivable

    shortfall = max(0, -capital_cents(bank_id, ctx.economy))
    if shortfall:
        insurance_cap = (
            ctx.settings.economy.median_wage_cents * ctx.settings.banking.insurance_cap_months // 12
        )
        customer_accounts = [
            account
            for account in ctx.economy.ledger.accounts()
            if account.bank_id == bank_id
            and account.code in {"dep", "esc"}
            and account.balance_cents > 0
        ]
        insurable = sum(min(account.balance_cents, insurance_cap) for account in customer_accounts)
        covered = min(shortfall, insurable)
        if covered:
            treasury = treasury_account(ctx.economy)
            expected = ctx.economy.ledger.next_txn_id(tick)
            insurance_event = emit(
                NewEvent(
                    DEPOSIT_INSURANCE_PAID,
                    {
                        "bank_id": bank_id,
                        "covered_cents": covered,
                        "depositors_n": len(customer_accounts),
                        "txn_id": str(expected),
                    },
                    subject_ids=(bank_id,),
                )
            )
            transaction_id = ctx.economy.ledger.post_transaction(
                (
                    Leg(treasury, -1, covered, "transfer"),
                    Leg(bank.reserve_account_id, 1, covered, "transfer"),
                ),
                tick=tick,
                cause=insurance_event,
                allow_negative=frozenset({treasury}),
            )
            if transaction_id != expected:
                raise RuntimeError("deposit insurance transaction ordinal diverged")
            events.append(insurance_event)

        residual = max(0, -capital_cents(bank_id, ctx.economy))
        if residual:
            balances = {account.account_id: account.balance_cents for account in customer_accounts}
            uninsured = {
                account.account_id: max(0, account.balance_cents - insurance_cap)
                for account in customer_accounts
            }
            weights = uninsured if sum(uninsured.values()) >= residual else balances
            haircut_total = min(residual, sum(weights.values()))
            haircuts = allocate(
                haircut_total,
                tuple((account_id, value) for account_id, value in sorted(weights.items())),
            )
            for account_id, cents in sorted(haircuts.items()):
                if cents <= 0:
                    continue
                owner_id = parse_account_id(account_id)[1]
                expected = ctx.economy.ledger.next_txn_id(tick)
                haircut_event = emit(
                    NewEvent(
                        DEPOSIT_HAIRCUT,
                        {
                            "bank_id": bank_id,
                            "depositor_id": owner_id,
                            "haircut_cents": cents,
                            "recovery_bp": (
                                10_000
                                * (balances[account_id] - cents)
                                // max(1, balances[account_id])
                            ),
                            "txn_id": str(expected),
                        },
                        subject_ids=(owner_id, bank_id),
                    )
                )
                transaction_id = ctx.economy.ledger.post_transaction(
                    (
                        Leg(account_id, -1, cents, "write_off"),
                        Leg(
                            bank.deposit_liability_account_id,
                            1,
                            cents,
                            "write_off",
                        ),
                    ),
                    tick=tick,
                    cause=haircut_event,
                )
                if transaction_id != expected:
                    raise RuntimeError("deposit haircut transaction ordinal diverged")
                events.append(haircut_event)

    if resolution == "assume":
        for loan in sorted(ctx.economy.loans.values(), key=lambda row: row.loan_id):
            if loan.lender_id != bank_id or loan.status != "current":
                continue
            new_receivable = ctx.economy.ledger.open_account(
                "lnr",
                primary_buyer.bank_id,
                "bank",
                ref=loan.loan_id,
                tick=tick,
            )
            ctx.economy.ledger.post_transaction(
                (
                    Leg(
                        loan.lender_receivable_account_id,
                        -1,
                        loan.outstanding_cents,
                        "loan",
                    ),
                    Leg(new_receivable, 1, loan.outstanding_cents, "loan"),
                ),
                tick=tick,
                cause=failed_event,
            )
            loan.lender_id = primary_buyer.bank_id
            loan.lender_receivable_account_id = new_receivable

        for account in sorted(
            (
                row
                for row in ctx.economy.ledger.accounts()
                if row.bank_id == bank_id and row.code in {"dep", "esc"} and row.balance_cents > 0
            ),
            key=lambda row: row.account_id,
        ):
            code, owner_id, _old_bank, ref = parse_account_id(account.account_id)
            destination = next(
                (
                    account_id
                    for account_id in ctx.economy.ledger.accounts_of(owner_id)
                    if parse_account_id(account_id)[0] == code
                    and parse_account_id(account_id)[2] == primary_buyer.bank_id
                    and parse_account_id(account_id)[3] == ref
                ),
                None,
            )
            if destination is None:
                destination = ctx.economy.ledger.open_account(
                    code,
                    owner_id,
                    account.owner_type,
                    bank_id=primary_buyer.bank_id,
                    ref=ref,
                    tick=tick,
                )
            ctx.economy.ledger.post_transaction(
                (
                    Leg(account.account_id, -1, account.balance_cents, "transfer"),
                    Leg(
                        bank.deposit_liability_account_id,
                        1,
                        account.balance_cents,
                        "transfer",
                    ),
                    Leg(
                        primary_buyer.deposit_liability_account_id,
                        -1,
                        account.balance_cents,
                        "transfer",
                    ),
                    Leg(destination, 1, account.balance_cents, "transfer"),
                ),
                tick=tick,
                cause=failed_event,
            )

        reserve_balance = ctx.economy.ledger.balance(bank.reserve_account_id)
        if reserve_balance > 0:
            ctx.economy.ledger.post_transaction(
                (
                    Leg(bank.reserve_account_id, -1, reserve_balance, "transfer"),
                    Leg(
                        primary_buyer.reserve_account_id,
                        1,
                        reserve_balance,
                        "transfer",
                    ),
                ),
                tick=tick,
                cause=failed_event,
            )
    else:
        for account in sorted(
            (
                row
                for row in ctx.economy.ledger.accounts()
                if row.bank_id == bank_id and row.code in {"dep", "esc"} and row.balance_cents > 0
            ),
            key=lambda row: row.account_id,
        ):
            code, owner_id, _old_bank, _ref = parse_account_id(account.account_id)
            cash = next(
                (
                    account_id
                    for account_id in ctx.economy.ledger.accounts_of(owner_id)
                    if parse_account_id(account_id)[0] == "cash"
                ),
                None,
            )
            if cash is None:
                cash = ctx.economy.ledger.open_account(
                    "cash",
                    owner_id,
                    account.owner_type,
                    tick=tick,
                )
            balance = account.balance_cents
            if ctx.economy.ledger.balance(bank.reserve_account_id) < balance:
                raise RuntimeError(f"liquidation left bank {bank_id} unable to pay {code} account")
            ctx.economy.ledger.post_transaction(
                (
                    Leg(account.account_id, -1, balance, "withdrawal"),
                    Leg(bank.deposit_liability_account_id, 1, balance, "withdrawal"),
                    Leg(bank.reserve_account_id, -1, balance, "withdrawal"),
                    Leg(cash, 1, balance, "withdrawal"),
                ),
                tick=tick,
                cause=failed_event,
            )

    ctx.economy.bond_holdings_cents.setdefault(primary_buyer.bank_id, {}).update(
        ctx.economy.bond_holdings_cents.pop(bank_id, {})
    )
    for bond in ctx.economy.bonds.values():
        if bond.holder_id == bank_id:
            bond.holder_id = primary_buyer.bank_id
    bank.status = "failed"
    bank.failed_tick = tick
    bank.lending_frozen = True
    return tuple(events)


@mechanism(
    "banking.interbank_refusal",
    entails=(
        "A bank below the interbank minimum capital ratio is refused peer funding and "
        "must use the penalty-rate discount window. The channel is assumed; whether "
        "contagion propagates, how far, and how fast remain outcomes."
    ),
    config_key="mechanisms.banking_interbank_refusal",
)
def settle_banks(
    tick: int,
    *,
    ctx: CentralContext,
    credit: CreditContext,
    emit: Emit,
) -> tuple[Event, ...]:
    _credit_context(ctx, credit)
    events: list[Event] = []
    commercial = [
        bank
        for bank in ctx.economy.banks.values()
        if not bank.is_central and bank.status == "active"
    ]
    required = {
        bank.bank_id: bp(
            deposits_cents(bank.bank_id, ctx.economy),
            bank.reserve_ratio_bp,
        )
        for bank in commercial
    }
    shorts = sorted(
        (
            (bank, required[bank.bank_id] - reserves_cents(bank.bank_id, ctx.economy))
            for bank in commercial
            if reserves_cents(bank.bank_id, ctx.economy) < required[bank.bank_id]
        ),
        key=lambda item: (-item[1], item[0].bank_id),
    )
    for borrower, _initial_shortfall in shorts:
        shortfall = max(
            0,
            required[borrower.bank_id] - reserves_cents(borrower.bank_id, ctx.economy),
        )
        longs = sorted(
            (
                (
                    bank,
                    reserves_cents(bank.bank_id, ctx.economy) - required[bank.bank_id],
                )
                for bank in commercial
                if bank.bank_id != borrower.bank_id
                and reserves_cents(bank.bank_id, ctx.economy) > required[bank.bank_id]
            ),
            key=lambda item: (-item[1], item[0].bank_id),
        )
        ctx.rng.get("banking.interbank", borrower.bank_id, tick).shuffle(longs)
        for lender, excess in longs:
            if shortfall <= 0:
                break
            amount = min(shortfall, excess)
            if capital_ratio_bp(borrower.bank_id, credit) < (
                ctx.settings.banking.interbank_min_ratio_bp
            ):
                events.append(
                    emit(
                        NewEvent(
                            INTERBANK_REFUSED,
                            {
                                "borrower_bank_id": borrower.bank_id,
                                "lender_bank_id": lender.bank_id,
                                "cents": amount,
                                "reason": "capital_ratio",
                            },
                            actor_id=lender.bank_id,
                            subject_ids=(borrower.bank_id,),
                        )
                    )
                )
                continue
            exposure = sum(
                loan.outstanding_cents
                for loan in ctx.economy.loans.values()
                if loan.lender_id == lender.bank_id
                and loan.borrower_id == borrower.bank_id
                and loan.status in {"current", "delinquent", "default"}
            )
            cap = bp(
                max(0, capital_cents(lender.bank_id, ctx.economy)),
                ctx.settings.banking.interbank_concentration_bp,
            )
            if exposure + amount > cap:
                events.append(
                    emit(
                        NewEvent(
                            INTERBANK_REFUSED,
                            {
                                "borrower_bank_id": borrower.bank_id,
                                "lender_bank_id": lender.bank_id,
                                "cents": amount,
                                "reason": "concentration",
                            },
                            actor_id=lender.bank_id,
                            subject_ids=(borrower.bank_id,),
                        )
                    )
                )
                continue
            events.extend(
                _originate_interbank(
                    lender.bank_id,
                    borrower.bank_id,
                    amount,
                    tick,
                    ctx=ctx,
                    credit=credit,
                    emit=emit,
                )
            )
            shortfall -= amount
        if shortfall > 0:
            events.extend(discount_window(borrower.bank_id, shortfall, tick, ctx=ctx, emit=emit))

    for bank in sorted(commercial, key=lambda row: row.bank_id):
        capital = capital_cents(bank.bank_id, ctx.economy)
        risk_assets = rwa_cents(bank.bank_id, credit)
        ratio = capital_ratio_bp(bank.bank_id, credit)
        bank.capital_cents = capital
        bank.capital_ratio_bp = ratio
        bank.lending_frozen = ratio < ctx.settings.banking.capital_ratio_min_bp
        total_loans = sum(
            loan.outstanding_cents
            for loan in ctx.economy.loans.values()
            if loan.lender_id == bank.bank_id
            and loan.status in {"current", "delinquent", "default"}
        )
        npl = sum(
            loan.outstanding_cents
            for loan in ctx.economy.loans.values()
            if loan.lender_id == bank.bank_id and loan.status == "default"
        )
        events.append(
            emit(
                NewEvent(
                    BANK_RATIOS_COMPUTED,
                    {
                        "bank_id": bank.bank_id,
                        "capital_cents": capital,
                        "rwa_cents": risk_assets,
                        "capital_ratio_bp": ratio,
                        "reserve_ratio_bp": (
                            10_000
                            * reserves_cents(bank.bank_id, ctx.economy)
                            // max(1, deposits_cents(bank.bank_id, ctx.economy))
                        ),
                        "ldr_bp": (
                            10_000
                            * total_loans
                            // max(1, deposits_cents(bank.bank_id, ctx.economy))
                        ),
                        "npl_bp": 10_000 * npl // max(1, total_loans),
                    },
                    subject_ids=(bank.bank_id,),
                )
            )
        )
        if bank.lending_frozen:
            events.append(
                emit(
                    NewEvent(
                        BANK_UNDERCAPITALISED,
                        {
                            "bank_id": bank.bank_id,
                            "capital_ratio_bp": ratio,
                            "threshold_bp": ctx.settings.banking.capital_ratio_min_bp,
                            "new_lending_frozen": True,
                        },
                        subject_ids=(bank.bank_id,),
                    )
                )
            )
        if capital < 0:
            events.extend(
                resolve_failure(
                    bank.bank_id,
                    tick,
                    ctx=ctx,
                    credit=credit,
                    emit=emit,
                )
            )
    return tuple(events)
